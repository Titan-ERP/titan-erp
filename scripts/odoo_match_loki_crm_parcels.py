from __future__ import annotations

"""Match LOKI CRM leads to parcel identifiers without storing parcel geometry.

The workflow is read-only unless every apply control is deliberately enabled.
ArcGIS polygon geometry is used by the remote service for the spatial predicate and
is never requested or persisted locally or in Odoo.
"""

import argparse
import json
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.odoo_runtime import ApplyGate, OdooClient, OdooConfig
from scripts.odoo_runtime.safety import append_audit

WORKFLOW = "match_loki_crm_parcels"
MODEL = "loki.crm.parcel.link"
SOURCE_KEY = "dallas_cad"
DEFAULT_LAYER_URL = (
    "https://gis.dallascityhall.com/arcgis/rest/services/"
    "Basemap/DallasTaxParcels/FeatureServer/0"
)
DEFAULT_AUDIT = Path("outputs/loki/crm_parcel_match_audit.jsonl")
DEFAULT_ENV = Path("odoo_connection.env")
DEFAULT_LIMIT = 100
MAX_LIMIT = 500
MINIMUM_FREE_BYTES = 2 * 1024**3
REQUIRED_LINK_FIELDS = {
    "company_id",
    "crm_lead_id",
    "partner_id",
    "source_key",
    "parcel_account",
    "parcel_external_id",
    "county",
    "state_code",
    "match_method",
    "confidence",
    "review_state",
    "latitude",
    "longitude",
    "source_url",
    "matched_at",
    "evidence_json",
    "active",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


def odoo_datetime(value: datetime) -> str:
    return value.astimezone(UTC).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")


def normalized_county(value: object) -> str:
    text = " ".join(str(value or "").strip().lower().split())
    return text.removesuffix(" county").strip()


def many2one_id(value: object) -> int | None:
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    if isinstance(value, int):
        return value
    return None


def valid_coordinate(value: object, *, minimum: float, maximum: float) -> float | None:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


@dataclass(frozen=True)
class CoverageBox:
    west: float
    south: float
    east: float
    north: float

    def contains(self, latitude: float, longitude: float) -> bool:
        return self.west <= longitude <= self.east and self.south <= latitude <= self.north


# A conservative Dallas County screening envelope. The ArcGIS polygon query remains
# authoritative; the envelope only avoids sending clearly out-of-county records.
DALLAS_COVERAGE = CoverageBox(west=-97.06, south=32.54, east=-96.43, north=33.03)


@dataclass(frozen=True)
class LeadPoint:
    lead_id: int
    partner_id: int
    latitude: float
    longitude: float
    lead_name: str = ""


@dataclass(frozen=True)
class MatchResult:
    status: str
    point: LeadPoint
    reason: str
    values: dict[str, Any] | None = None
    candidate_count: int = 0


class ArcGISRequestError(RuntimeError):
    pass


class ArcGISParcelAdapter:
    """County adapter backed by an ArcGIS FeatureServer point intersection."""

    def __init__(
        self,
        *,
        layer_url: str = DEFAULT_LAYER_URL,
        county: str = "Dallas",
        state_code: str = "TX",
        coverage: CoverageBox = DALLAS_COVERAGE,
        timeout_seconds: float = 20,
        attempts: int = 3,
        minimum_interval_seconds: float = 0.15,
        opener: Callable[..., Any] = urllib.request.urlopen,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.layer_url = layer_url.rstrip("/")
        self.county = county
        self.state_code = state_code
        self.coverage = coverage
        self.timeout_seconds = timeout_seconds
        self.attempts = max(1, attempts)
        self.minimum_interval_seconds = max(0.0, minimum_interval_seconds)
        self.opener = opener
        self.sleep = sleep
        self._last_request_at = 0.0

    def supports(self, point: LeadPoint) -> bool:
        return self.coverage.contains(point.latitude, point.longitude)

    def _query_url(self, point: LeadPoint) -> str:
        params = {
            "f": "json",
            "where": "1=1",
            "geometry": f"{point.longitude:.8f},{point.latitude:.8f}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "OBJECTID,ACCT,GIS_ACCT,COUNTY,APPRAISALYEAR,Website",
            "returnGeometry": "false",
        }
        return f"{self.layer_url}/query?{urllib.parse.urlencode(params)}"

    def _request_json(self, url: str) -> dict[str, Any]:
        last_error: Exception | None = None
        for attempt in range(1, self.attempts + 1):
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < self.minimum_interval_seconds:
                self.sleep(self.minimum_interval_seconds - elapsed)
            request = urllib.request.Request(
                url,
                headers={
                    "Accept": "application/json",
                    "User-Agent": "Titan-LOKI-parcel-matcher/1.0",
                },
            )
            try:
                with self.opener(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self._last_request_at = time.monotonic()
                if not isinstance(payload, dict):
                    raise ArcGISRequestError("ArcGIS returned a non-object response.")
                if payload.get("error"):
                    raise ArcGISRequestError(f"ArcGIS error: {payload['error']}")
                return payload
            except (OSError, TimeoutError, urllib.error.URLError, json.JSONDecodeError, ArcGISRequestError) as exc:
                last_error = exc
                self._last_request_at = time.monotonic()
                if attempt == self.attempts:
                    break
                self.sleep(min(2 ** (attempt - 1), 8))
        raise ArcGISRequestError(f"ArcGIS request failed after {self.attempts} attempts: {last_error}")

    def match(self, point: LeadPoint, *, company_id: int, matched_at: datetime | None = None) -> MatchResult:
        if not self.supports(point):
            return MatchResult("unsupported", point, "outside_dallas_adapter_extent")

        payload = self._request_json(self._query_url(point))
        raw_features = payload.get("features", [])
        if not isinstance(raw_features, list):
            raise ArcGISRequestError("ArcGIS response has an invalid features value.")
        features = [item for item in raw_features if isinstance(item, Mapping)]
        county_features = [
            item
            for item in features
            if normalized_county((item.get("attributes") or {}).get("COUNTY"))
            == normalized_county(self.county)
        ]
        if features and not county_features:
            return MatchResult("unsupported", point, "point_resolved_to_other_county", candidate_count=len(features))
        if not county_features:
            return MatchResult("unmatched", point, "no_intersecting_dallas_parcel")
        if len(county_features) != 1:
            return MatchResult(
                "review",
                point,
                "multiple_intersecting_dallas_parcels",
                candidate_count=len(county_features),
            )

        attributes = dict(county_features[0].get("attributes") or {})
        parcel_account = str(attributes.get("GIS_ACCT") or attributes.get("ACCT") or "").strip()
        object_id = str(attributes.get("OBJECTID") or "").strip()
        if not parcel_account or not object_id:
            return MatchResult("review", point, "parcel_identifier_missing", candidate_count=1)

        timestamp = matched_at or utc_now()
        evidence = {
            "schema_version": "1.0",
            "spatial_relation": "esriSpatialRelIntersects",
            "input_spatial_reference": 4326,
            "arcgis_object_id": object_id,
            "arcgis_account": str(attributes.get("ACCT") or ""),
            "arcgis_gis_account": str(attributes.get("GIS_ACCT") or ""),
            "appraisal_year": attributes.get("APPRAISALYEAR"),
            "county": attributes.get("COUNTY"),
            "geometry_requested": False,
        }
        values = {
            "company_id": company_id,
            "crm_lead_id": point.lead_id,
            "partner_id": point.partner_id,
            "source_key": SOURCE_KEY,
            "parcel_account": parcel_account,
            "parcel_external_id": object_id,
            "county": self.county,
            "state_code": self.state_code,
            "match_method": "point_in_polygon",
            "confidence": 1.0,
            "review_state": "matched",
            "latitude": point.latitude,
            "longitude": point.longitude,
            "source_url": self.layer_url,
            "matched_at": odoo_datetime(timestamp),
            "evidence_json": evidence,
            "active": True,
        }
        return MatchResult("matched", point, "single_intersecting_dallas_parcel", values, 1)


def resolve_single_id(client: OdooClient, model: str, domain: list[Any], label: str) -> int:
    ids = client.execute(model, "search", [domain], {"limit": 2, "order": "id"})
    if len(ids) != 1:
        raise RuntimeError(f"Expected exactly one {label}; found {len(ids)}.")
    return int(ids[0])


def fetch_loki_lead_points(
    client: OdooClient,
    *,
    company_name: str,
    team_name: str,
    limit: int,
) -> tuple[int, list[LeadPoint], list[dict[str, Any]]]:
    company_id = resolve_single_id(client, "res.company", [["name", "=", company_name]], f"company named {company_name!r}")
    team_id = resolve_single_id(
        client,
        "crm.team",
        [["name", "=", team_name], ["company_id", "in", [company_id, False]]],
        f"CRM team named {team_name!r}",
    )
    leads = client.execute(
        "crm.lead",
        "search_read",
        [[
            ["company_id", "=", company_id],
            ["team_id", "=", team_id],
            ["partner_id", "!=", False],
        ]],
        {"fields": ["id", "name", "partner_id"], "limit": limit, "order": "id"},
    )
    partner_ids = sorted({many2one_id(row.get("partner_id")) for row in leads} - {None})
    partner_fields = client.fields("res.partner")
    latitude_field = "partner_latitude" if "partner_latitude" in partner_fields else "latitude"
    longitude_field = "partner_longitude" if "partner_longitude" in partner_fields else "longitude"
    if latitude_field not in partner_fields or longitude_field not in partner_fields:
        raise RuntimeError("res.partner has no supported latitude/longitude fields.")
    partners = client.execute(
        "res.partner",
        "read",
        [partner_ids],
        {"fields": [latitude_field, longitude_field]},
    ) if partner_ids else []
    by_id = {int(row["id"]): row for row in partners}

    points: list[LeadPoint] = []
    skipped: list[dict[str, Any]] = []
    for lead in leads:
        partner_id = many2one_id(lead.get("partner_id"))
        partner = by_id.get(partner_id or -1, {})
        latitude = valid_coordinate(partner.get(latitude_field), minimum=-90, maximum=90)
        longitude = valid_coordinate(partner.get(longitude_field), minimum=-180, maximum=180)
        if partner_id is None or latitude is None or longitude is None or (latitude == 0 and longitude == 0):
            skipped.append({"lead_id": int(lead["id"]), "status": "skipped", "reason": "missing_valid_partner_coordinates"})
            continue
        points.append(LeadPoint(int(lead["id"]), partner_id, latitude, longitude, str(lead.get("name") or "")))
    return company_id, points, skipped


def ensure_link_contract(client: OdooClient) -> None:
    actual = set(client.fields(MODEL))
    missing = sorted(REQUIRED_LINK_FIELDS - actual)
    if missing:
        raise RuntimeError(f"{MODEL} is missing required fields: {', '.join(missing)}")


def upsert_link(client: OdooClient, values: Mapping[str, Any]) -> tuple[str, int]:
    domain = [
        ["company_id", "=", values["company_id"]],
        ["crm_lead_id", "=", values["crm_lead_id"]],
        ["source_key", "=", values["source_key"]],
    ]
    existing = client.execute(MODEL, "search", [domain], {"limit": 2, "order": "id"})
    if len(existing) > 1:
        raise RuntimeError(f"Duplicate {MODEL} rows already exist for lead {values['crm_lead_id']} and source {values['source_key']}.")
    payload = dict(values)
    if existing:
        record_id = int(existing[0])
        client.execute(MODEL, "write", [[record_id], payload])
        return "updated", record_id
    return "created", int(client.execute(MODEL, "create", [payload]))


def audit_result(path: Path, result: MatchResult, *, mode: str, write_action: str | None = None, record_id: int | None = None) -> None:
    row = {
        "schema_version": "1.0",
        "workflow": WORKFLOW,
        "mode": mode,
        "logged_at_utc": utc_now().isoformat(),
        "lead_id": result.point.lead_id,
        "partner_id": result.point.partner_id,
        "status": result.status,
        "reason": result.reason,
        "candidate_count": result.candidate_count,
        "parcel_account": (result.values or {}).get("parcel_account"),
        "parcel_external_id": (result.values or {}).get("parcel_external_id"),
        "write_action": write_action,
        "link_record_id": record_id,
    }
    append_audit(path, row)


def ensure_audit_disk_space(path: Path, minimum_free_bytes: int = MINIMUM_FREE_BYTES) -> None:
    probe = path.resolve().parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(probe).free
    if free < minimum_free_bytes:
        raise RuntimeError(f"Audit drive has only {free} bytes free; at least {minimum_free_bytes} are required.")


def bounded_limit(value: str) -> int:
    parsed = int(value)
    if not 1 <= parsed <= MAX_LIMIT:
        raise argparse.ArgumentTypeError(f"limit must be between 1 and {MAX_LIMIT}")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--audit", type=Path, default=DEFAULT_AUDIT)
    parser.add_argument("--company", default="LOKI")
    parser.add_argument("--team", default="LOKI CRM")
    parser.add_argument("--limit", type=bounded_limit, default=DEFAULT_LIMIT)
    parser.add_argument("--layer-url", default=DEFAULT_LAYER_URL)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--attempts", type=int, default=3)
    parser.add_argument("--request-interval", type=float, default=0.15)
    parser.add_argument("--apply", action="store_true", help="Enable writes only when all other write controls pass.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    return parser.parse_args(argv)


def run(args: argparse.Namespace, *, client: OdooClient | None = None, adapter: ArcGISParcelAdapter | None = None) -> dict[str, int]:
    ensure_audit_disk_space(args.audit)
    odoo = client or OdooClient(OdooConfig.from_env(args.env_file)).connect()
    company_id, points, skipped = fetch_loki_lead_points(
        odoo, company_name=args.company, team_name=args.team, limit=args.limit
    )
    parcel_adapter = adapter or ArcGISParcelAdapter(
        layer_url=args.layer_url,
        timeout_seconds=args.timeout,
        attempts=args.attempts,
        minimum_interval_seconds=args.request_interval,
    )
    results = [parcel_adapter.match(point, company_id=company_id) for point in points]
    matched = [result for result in results if result.status == "matched" and result.values]
    mode = "apply" if args.apply else "dry_run"

    gate = ApplyGate(WORKFLOW, args.apply, args.confirm, args.reason, args.limit)
    if args.apply:
        ensure_link_contract(odoo)
        gate.authorize(len(matched))

    for row in skipped:
        append_audit(args.audit, {"schema_version": "1.0", "workflow": WORKFLOW, "mode": mode, "logged_at_utc": utc_now().isoformat(), **row})
    counts = {"matched": 0, "unmatched": 0, "unsupported": 0, "review": 0, "skipped": len(skipped), "created": 0, "updated": 0}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
        action = None
        record_id = None
        if args.apply and result.values:
            action, record_id = upsert_link(odoo, result.values)
            counts[action] += 1
        audit_result(args.audit, result, mode=mode, write_action=action, record_id=record_id)
    append_audit(
        args.audit,
        {
            "schema_version": "1.0",
            "workflow": WORKFLOW,
            "mode": mode,
            "logged_at_utc": utc_now().isoformat(),
            "event": "summary",
            "limit": args.limit,
            "counts": counts,
        },
    )
    return counts


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    counts = run(args)
    print(json.dumps({"mode": "apply" if args.apply else "dry_run", "counts": counts, "audit": str(args.audit)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
