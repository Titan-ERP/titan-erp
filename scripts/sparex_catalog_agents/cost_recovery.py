"""Deterministic, throttled recovery of exact Sparex dealer prices.

The worker never asks a model to select or interpret a price.  It accepts only
one positive USD value inside the exact ``priceb_<sku digits>`` container on an
authenticated exact-SKU page, then asks Odoo to synchronize supplier cost,
standard cost, and provisional cost-plus pricing under a hash-verified rollback
snapshot.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

import requests
from lxml import html as lxml_html

from scripts.odoo_runtime import ArtifactStore, OdooClient
from scripts.sparex_catalog_discovery import (
    PortalCooldownError,
    _checked_request,
    authenticated_session,
    exact_sparex_product_url,
)

COST_RECOVERY_CONFIRMATION = "sparex-dealer-cost-recovery"
PARSER_VERSION = "sparex-exact-priceb-v1"
PORTAL_COOLDOWN_MINUTES = 60
MAX_COST_RECOVERY_BATCH = 50
MAX_SOURCE_IMAGE_BYTES = 10 * 1024 * 1024
MONEY_PATTERN = re.compile(r"(?<![0-9])\$\s*(?P<price>[0-9][0-9,]*(?:\.\d{1,2})?)(?![0-9])")


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_exact_priceb(content: bytes | str, sku: str) -> dict[str, Any]:
    """Return one exact positive USD dealer price or a bounded failure code."""
    digits = re.sub(r"\D", "", sku or "")
    if not digits:
        return {"status": "sku_invalid"}
    document = lxml_html.fromstring(content)
    titles = document.xpath("//main//h1[@itemprop='name']")
    if len(titles) != 1 or not " ".join(titles[0].itertext()).strip():
        return {"status": "identity_incomplete"}
    containers = document.xpath(f"//*[@id='priceb_{digits}']")
    if len(containers) > 1:
        return {"status": "price_container_ambiguous"}
    if not containers:
        html = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
        prices = {
            round(float(match.group("price").replace(",", "")), 2)
            for match in re.finditer(
                r'["\']final_price["\']\s*:\s*(?P<price>[0-9][0-9,]*(?:\.\d+)?)',
                html,
                flags=re.IGNORECASE,
            )
            if float(match.group("price").replace(",", "")) > 0
        }
        if not prices:
            return {"status": "price_container_absent"}
        if len(prices) != 1:
            return {"status": "price_ambiguous"}
        return {"status": "accepted", "price": next(iter(prices)), "currency": "USD"}
    container = containers[0]
    values: set[float] = set()
    for node in [container, *container.xpath(".//*")]:
        for attribute in ("data-price-amount", "data-price", "content", "value"):
            raw = (node.get(attribute) or "").strip().replace(",", "")
            if re.fullmatch(r"[0-9]+(?:\.[0-9]{1,2})?", raw):
                value = round(float(raw), 2)
                if value > 0:
                    values.add(value)
    text = " ".join(" ".join(container.itertext()).split())
    for match in MONEY_PATTERN.finditer(text):
        value = round(float(match.group("price").replace(",", "")), 2)
        if value > 0:
            values.add(value)
    if not values:
        return {"status": "price_absent"}
    if len(values) != 1:
        return {"status": "price_ambiguous"}
    return {"status": "accepted", "price": next(iter(values)), "currency": "USD"}


def parse_detail_image_url(content: bytes | str, page_url: str) -> str:
    """Return one canonical HTTPS product image exposed by an exact detail page."""
    document = lxml_html.fromstring(content)
    raw_candidates = document.xpath(
        "//meta[translate(@property, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')='og:image']/@content"
        " | //main//*[@itemprop='image']/@content"
        " | //main//img[@itemprop='image']/@data-zoom-image"
        " | //main//img[@itemprop='image']/@data-src"
        " | //main//img[@itemprop='image']/@src"
    )
    candidates = []
    for raw in raw_candidates:
        candidate = urljoin(page_url, str(raw or "").strip())
        parsed = urlsplit(candidate)
        if parsed.scheme.casefold() == "https" and parsed.hostname and not candidate.startswith("data:"):
            candidates.append(candidate)
    unique = list(dict.fromkeys(candidates))
    return unique[0] if len(unique) == 1 else ""


def _archive(store: ArtifactStore, name: str, payload: Any, bucket: str, prefix: str) -> dict[str, Any]:
    record_count = len(payload.get("records") or []) if isinstance(payload, dict) else 1
    record = store.write_json(name, payload, record_count=record_count)
    return store.archive_s3(record, bucket=bucket, prefix=prefix)


def _cooldown_path(artifact_root: Path) -> Path:
    return artifact_root / "dealer-cost-portal-cooldown.json"


def cooldown_active(artifact_root: Path, now: datetime | None = None) -> bool:
    path = _cooldown_path(artifact_root)
    if not path.exists():
        return False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        until = datetime.fromisoformat(str(payload["until_utc"]))
    except (KeyError, ValueError, json.JSONDecodeError):
        return True
    return until > (now or datetime.now(UTC))


def write_cooldown(artifact_root: Path, error_code: str) -> None:
    artifact_root.mkdir(parents=True, exist_ok=True)
    until = datetime.now(UTC) + timedelta(minutes=PORTAL_COOLDOWN_MINUTES)
    _cooldown_path(artifact_root).write_text(
        json.dumps({"until_utc": until.isoformat(), "error_code": error_code}, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def recover_dealer_costs(
    client: OdooClient,
    *,
    worker_id: str,
    limit: int,
    dealer_env_file: Path,
    throttle_seconds: float,
    store: ArtifactStore,
    artifact_root: Path,
    s3_bucket: str,
    s3_prefix: str,
    reason: str,
) -> dict[str, Any]:
    bounded = max(1, min(int(limit or 1), MAX_COST_RECOVERY_BATCH))
    if cooldown_active(artifact_root):
        return {"state": "portal_cooldown", "claimed": 0, "accepted": 0, "applied": 0, "write_blocked": True}
    claims = client.call(
        "southern.sparex.discovery.item",
        "claim_cost_recovery_batch",
        worker_id=worker_id,
        limit=bounded,
    )
    plan = {
        "schema_version": "1.0",
        "workflow": COST_RECOVERY_CONFIRMATION,
        "parser_version": PARSER_VERSION,
        "worker_id": worker_id,
        "reason": reason,
        "records": claims,
    }
    plan_record = _archive(store, "dealer-cost-plan.json", plan, s3_bucket, s3_prefix)
    if not claims:
        return {
            "state": "succeeded",
            "claimed": 0,
            "accepted": 0,
            "applied": 0,
            "plan_sha256": plan_record["sha256"],
            "plan_uri": plan_record["artifact_uri"],
        }

    accepted: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []
    try:
        session, throttle, _products_url = authenticated_session(dealer_env_file, throttle_seconds)
        for claim in claims:
            if int(claim.get("supplierinfo_count") or 0) != 1:
                client.call(
                    "southern.sparex.discovery.item",
                    "record_cost_recovery_result",
                    item_id=claim["item_id"],
                    worker_id=worker_id,
                    outcome="manual_review",
                    error_code="existing_supplier_line_not_unique",
                )
                outcomes.append({"item_id": claim["item_id"], "sku": claim["sku"], "status": "manual_review"})
                continue
            url = str(claim.get("source_url") or "").strip()
            if (
                not exact_sparex_product_url(url, claim["sku"])
                or hashlib.sha256(url.encode()).hexdigest() != claim.get("source_url_sha256")
            ):
                client.call(
                    "southern.sparex.discovery.item",
                    "record_cost_recovery_result",
                    item_id=claim["item_id"],
                    worker_id=worker_id,
                    outcome="manual_review",
                    error_code="exact_source_contract_failed",
                )
                outcomes.append({"item_id": claim["item_id"], "sku": claim["sku"], "status": "manual_review"})
                continue
            try:
                response = _checked_request(session, throttle, "GET", url)
            except requests.HTTPError as exc:
                status = int(exc.response.status_code) if exc.response is not None else 0
                outcome = "not_found" if status == 404 else "source_unavailable"
                client.call(
                    "southern.sparex.discovery.item",
                    "record_cost_recovery_result",
                    item_id=claim["item_id"],
                    worker_id=worker_id,
                    outcome=outcome,
                    error_code=f"portal_http_{status or 'error'}",
                )
                outcomes.append({"item_id": claim["item_id"], "sku": claim["sku"], "status": outcome})
                continue
            except requests.RequestException as exc:
                client.call(
                    "southern.sparex.discovery.item",
                    "record_cost_recovery_result",
                    item_id=claim["item_id"],
                    worker_id=worker_id,
                    outcome="source_unavailable",
                    error_code=f"transport_{type(exc).__name__}",
                )
                outcomes.append(
                    {"item_id": claim["item_id"], "sku": claim["sku"], "status": "source_unavailable"}
                )
                continue
            if urlsplit(response.url).path.casefold().startswith("/customer/account/login"):
                raise PortalCooldownError("dealer_session_lost")
            if response.url != url:
                client.call(
                    "southern.sparex.discovery.item",
                    "record_cost_recovery_result",
                    item_id=claim["item_id"],
                    worker_id=worker_id,
                    outcome="manual_review",
                    error_code="canonical_url_redirected",
                )
                outcomes.append({"item_id": claim["item_id"], "sku": claim["sku"], "status": "manual_review"})
                continue
            parsed = parse_exact_priceb(response.content, claim["sku"])
            if parsed["status"] != "accepted":
                client.call(
                    "southern.sparex.discovery.item",
                    "record_cost_recovery_result",
                    item_id=claim["item_id"],
                    worker_id=worker_id,
                    outcome="not_found",
                    error_code=parsed["status"],
                )
                outcomes.append({"item_id": claim["item_id"], "sku": claim["sku"], "status": parsed["status"]})
                continue
            image_evidence: dict[str, Any] = {}
            if not claim.get("has_image"):
                image_url = parse_detail_image_url(response.content, url)
                if image_url:
                    try:
                        image_response = _checked_request(session, throttle, "GET", image_url)
                    except requests.HTTPError as exc:
                        status = int(exc.response.status_code) if exc.response is not None else 0
                        if status == 404:
                            image_response = None
                        else:
                            raise PortalCooldownError(f"dealer_image_http_{status or 'error'}") from exc
                    except requests.RequestException as exc:
                        raise PortalCooldownError(f"dealer_image_{type(exc).__name__}") from exc
                    if image_response is None:
                        outcomes.append(
                            {"item_id": claim["item_id"], "sku": claim["sku"], "status": "image_not_found"}
                        )
                        image_url = ""
                if image_url:
                    content_type = str(image_response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
                    image_content = image_response.content
                    if (
                        urlsplit(image_response.url).scheme.casefold() != "https"
                        or not content_type.startswith("image/")
                        or not image_content
                        or len(image_content) > MAX_SOURCE_IMAGE_BYTES
                    ):
                        raise PortalCooldownError("dealer_image_content_invalid")
                    image_evidence = {
                        "detail_image_url": image_url,
                        "detail_image_url_sha256": hashlib.sha256(image_url.encode()).hexdigest(),
                        "detail_image_base64": base64.b64encode(image_content).decode("ascii"),
                        "detail_image_sha256": hashlib.sha256(image_content).hexdigest(),
                    }
            evidence = {
                **claim,
                "dealer_price": parsed["price"],
                "currency": parsed["currency"],
                "evidence_url": url,
                "evidence_url_sha256": claim["source_url_sha256"],
                "evidence_sha256": hashlib.sha256(response.content).hexdigest(),
                "parser_version": PARSER_VERSION,
                "retrieved_at_utc": datetime.now(UTC).isoformat(),
                **image_evidence,
            }
            accepted.append(evidence)
            outcomes.append({"item_id": claim["item_id"], "sku": claim["sku"], "status": "accepted"})
    except PortalCooldownError as exc:
        write_cooldown(artifact_root, str(exc))
        for claim in claims:
            try:
                client.call(
                    "southern.sparex.discovery.item",
                    "record_cost_recovery_result",
                    item_id=claim["item_id"],
                    worker_id=worker_id,
                    outcome="source_unavailable",
                    error_code="portal_cooldown",
                )
            except Exception as release_error:  # noqa: BLE001 - already-released claims are expected here
                outcomes.append(
                    {
                        "item_id": claim["item_id"],
                        "sku": claim["sku"],
                        "status": f"claim_release_{type(release_error).__name__}",
                    }
                )
        raise

    evidence_record = _archive(
        store,
        "dealer-cost-evidence.json",
        {"schema_version": "1.0", "parser_version": PARSER_VERSION, "records": accepted, "outcomes": outcomes},
        s3_bucket,
        s3_prefix,
    )
    rollback_record = _archive(
        store,
        "dealer-cost-rollback.json",
        {
            "schema_version": "1.0",
            "records": [
                {
                    "item_id": row["item_id"],
                    "product_id": row["product_id"],
                    "sku": row["sku"],
                    "supplierinfo_id": row["supplierinfo_id"],
                    "supplier_price_before": row["supplier_price_before"],
                    "standard_price_before": row["standard_price_before"],
                    "list_price_before": row["list_price_before"],
                    "quote_only_before": row["quote_only_before"],
                    "price_basis_before": row["price_basis_before"],
                    "cost_plus_margin_before": row["cost_plus_margin_before"],
                    "price_basis_updated_at_before": row["price_basis_updated_at_before"],
                    "snapshot_sha256": row["snapshot_sha256"],
                }
                for row in accepted
            ],
        },
        s3_bucket,
        s3_prefix,
    )
    applied: list[dict[str, Any]] = []
    error = ""
    try:
        if accepted:
            applied = client.call(
                "southern.sparex.discovery.item",
                "apply_cost_recovery_plan",
                records=accepted,
                worker_id=worker_id,
                confirmation=COST_RECOVERY_CONFIRMATION,
                reason=reason,
            )
            for row in applied:
                supplier = client.call(
                    "product.supplierinfo",
                    "read",
                    ids=[row["supplierinfo_id"]],
                    fields=["price", "product_tmpl_id"],
                )[0]
                if (
                    int(supplier["product_tmpl_id"][0]) != int(row["product_id"])
                    or abs(float(supplier["price"]) - float(row["supplier_price_applied"])) > 1e-9
                ):
                    raise RuntimeError("dealer_cost_post_write_verification_failed")
                product = client.call(
                    "product.template",
                    "read",
                    ids=[row["product_id"]],
                    fields=[
                        "standard_price",
                        "list_price",
                        "southern_quote_only",
                        "southern_price_basis",
                    ],
                )[0]
                if (
                    abs(float(product["standard_price"]) - float(row["standard_price_applied"])) > 1e-9
                    or abs(float(product["list_price"]) - float(row["list_price_applied"])) > 1e-9
                    or bool(product["southern_quote_only"]) != bool(row["quote_only_applied"])
                    or product["southern_price_basis"] != row["price_basis_applied"]
                ):
                    raise RuntimeError("cost_plus_post_write_verification_failed")
    except Exception as exc:  # noqa: BLE001 - every partial verified write must be rolled back
        error = f"{type(exc).__name__}:dealer_cost_apply_failed"
        if applied:
            client.call(
                "southern.sparex.discovery.item",
                "rollback_cost_recovery",
                records=applied,
                reason=error,
            )
        else:
            for row in accepted:
                try:
                    client.call(
                        "southern.sparex.discovery.item",
                        "record_cost_recovery_result",
                        item_id=row["item_id"],
                        worker_id=worker_id,
                        outcome="manual_review",
                        error_code="dealer_cost_apply_failed",
                    )
                except Exception as release_error:  # noqa: BLE001 - preserve the original apply failure
                    outcomes.append(
                        {
                            "item_id": row["item_id"],
                            "sku": row["sku"],
                            "status": f"claim_release_{type(release_error).__name__}",
                        }
                    )
    result = {
        "schema_version": "1.0",
        "workflow": COST_RECOVERY_CONFIRMATION,
        "plan_sha256": plan_record["sha256"],
        "evidence_sha256": evidence_record["sha256"],
        "rollback_sha256": rollback_record["sha256"],
        "claimed": len(claims),
        "accepted": len(accepted),
        "applied": 0 if error else len(applied),
        "outcomes": outcomes,
        "terminal_state": "failed" if error else "succeeded",
        "error": error or None,
    }
    result_record = _archive(store, "dealer-cost-result.json", result, s3_bucket, s3_prefix)
    return {
        **result,
        "plan_uri": plan_record["artifact_uri"],
        "evidence_uri": evidence_record["artifact_uri"],
        "rollback_uri": rollback_record["artifact_uri"],
        "result_sha256": result_record["sha256"],
        "result_uri": result_record["artifact_uri"],
        "write_blocked": bool(error),
    }
