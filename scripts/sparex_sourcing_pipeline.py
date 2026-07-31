"""Canonical supervised Sparex sourcing pipeline.

Stages are explicit and never select the newest local file implicitly:

plan -> source -> apply-evidence -> approve in Odoo -> publish

No stage writes product standard cost. Supplier-cost application is owned by the
Odoo sourcing queue and writes product.supplierinfo only after human approval.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote_plus, urljoin

import requests

if __package__:
    from scripts.odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig
    from scripts.odoo_runtime.client import load_env_file
    from scripts.odoo_runtime.safety import append_audit
else:
    from odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig
    from odoo_runtime.client import load_env_file
    from odoo_runtime.safety import append_audit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "odoo_connection.env"
ARTIFACT_ROOT = ROOT / "outputs" / "sparex_sourcing"
SCHEMA_VERSION = "1.0"
PARSER_VERSION = "2.0.0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return sha256_file(path)


def verify_explicit_input(path: Path, expected_sha256: str) -> dict[str, Any]:
    actual = sha256_file(path)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha256.casefold()):
        raise RuntimeError("--input-sha256 must be a SHA-256 hexadecimal value.")
    if actual != expected_sha256.casefold():
        raise RuntimeError(f"Input SHA-256 mismatch for {path}.")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError("Unsupported artifact schema version.")
    return payload


def clean_sku(value: Any) -> str:
    text = str(value or "").strip().upper().replace(" ", "")
    digits = re.sub(r"\D", "", text)
    return f"S.{digits}" if digits and text.startswith("S") else text


def money(value: Any) -> float:
    try:
        return round(float(str(value or "").replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return 0.0


def iter_json_nodes(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_json_nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_json_nodes(child)


def structured_price_candidates(html: str, sku: str) -> list[dict[str, Any]]:
    """Extract only SKU-anchored, product-specific price candidates."""
    normalized = clean_sku(sku)
    candidates: list[dict[str, Any]] = []
    scripts = re.findall(
        r"<script\b[^>]*type=[\"']application/ld\+json[\"'][^>]*>(.*?)</script>",
        html,
        flags=re.I | re.S,
    )
    for raw in scripts:
        try:
            payload = json.loads(unescape(raw).strip())
        except (json.JSONDecodeError, TypeError):
            continue
        for node in iter_json_nodes(payload):
            node_sku = clean_sku(node.get("sku") or node.get("mpn"))
            if node_sku and node_sku != normalized:
                continue
            node_type = str(node.get("@type") or "").casefold()
            if node_type and node_type not in {"product", "offer", "aggregateoffer"}:
                continue
            offers = node.get("offers") if node_type == "product" else node
            for offer in iter_json_nodes(offers):
                price = money(offer.get("price") or offer.get("lowPrice"))
                currency = str(offer.get("priceCurrency") or "USD").upper()
                if price > 0:
                    candidates.append({"price": price, "currency": currency, "method": "json_ld_offer"})

    itemprop_patterns = (
        r'itemprop=[\"\']price[\"\'][^>]*content=[\"\'](?P<price>[0-9][0-9,]*(?:\.\d{2})?)[\"\']',
        r'content=[\"\'](?P<price>[0-9][0-9,]*(?:\.\d{2})?)[\"\'][^>]*itemprop=[\"\']price[\"\']',
    )
    for pattern in itemprop_patterns:
        for match in re.finditer(pattern, html, flags=re.I):
            price = money(match.group("price"))
            if price > 0:
                candidates.append({"price": price, "currency": "USD", "method": "itemprop_price"})

    # Magento final_price is accepted only when it resolves to one unique value
    # on an already exact-SKU page. Generic dollar amounts are never considered.
    for match in re.finditer(r'[\"\']final_price[\"\']\s*:\s*(?P<price>[0-9][0-9,]*(?:\.\d+)?)', html, flags=re.I):
        price = money(match.group("price"))
        if price > 0:
            candidates.append({"price": price, "currency": "USD", "method": "magento_final_price"})
    return candidates


def choose_exact_price(html: str, sku: str) -> dict[str, Any]:
    candidates = structured_price_candidates(html, sku)
    unique = {(row["currency"], row["price"]) for row in candidates}
    if not unique:
        return {"status": "no_exact_price", "candidates": []}
    currencies = {currency for currency, _price in unique}
    prices = {price for _currency, price in unique}
    if len(currencies) != 1 or len(prices) != 1:
        return {"status": "ambiguous_price", "candidates": candidates}
    currency, price = next(iter(unique))
    return {
        "status": "accepted",
        "price": price,
        "currency": currency,
        "method": "+".join(sorted({row["method"] for row in candidates})),
        "candidate_count": len(candidates),
    }


def page_matches_sku(html: str, url: str, sku: str) -> bool:
    normalized = clean_sku(sku)
    digits = re.sub(r"\D", "", normalized)
    compact = re.sub(r"\s+", "", re.sub(r"<[^>]+>", " ", html)).upper()
    return normalized.replace(" ", "") in compact or bool(digits and re.search(rf"(?:S[.-]?)?{re.escape(digits)}(?:\.HTML)?", url, flags=re.I))


def odoo_client(env_file: Path) -> OdooClient:
    return OdooClient(OdooConfig.from_env(env_file.resolve())).connect()


def artifact_paths(stage: str) -> tuple[str, Path, Path]:
    run_id = f"{stage}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}"
    run_dir = ARTIFACT_ROOT / run_id
    return run_id, run_dir / f"{stage}.json", run_dir / "manifest.json"


def write_artifact(
    stage: str,
    payload: dict[str, Any],
    *,
    input_path: Path | None = None,
    input_sha: str = "",
    archive_s3: bool = False,
    s3_bucket: str = "",
    s3_prefix: str = "sparex-sourcing",
) -> dict[str, Any]:
    run_id, artifact_path, manifest_path = artifact_paths(stage)
    payload.update({"schema_version": SCHEMA_VERSION, "stage": stage, "run_id": run_id, "created_at_utc": utc_now()})
    artifact_sha = write_json(artifact_path, payload)
    archive = {}
    if archive_s3:
        if not s3_bucket:
            raise RuntimeError("--s3-bucket is required with --archive-s3.")
        store = ArtifactStore(artifact_path.parent, schema_version=SCHEMA_VERSION)
        archive = store.archive_s3(
            {
                "schema_version": SCHEMA_VERSION,
                "kind": "json",
                "path": str(artifact_path.resolve()),
                "sha256": artifact_sha,
                "bytes": artifact_path.stat().st_size,
                "record_count": len(payload.get("records") or []),
                "created_at_utc": payload["created_at_utc"],
            },
            bucket=s3_bucket,
            prefix=s3_prefix,
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "stage": stage,
        "created_at_utc": utc_now(),
        "artifact_path": str(artifact_path),
        "artifact_sha256": artifact_sha,
        "input_path": str(input_path) if input_path else "",
        "input_sha256": input_sha,
        "parser_version": PARSER_VERSION,
        "archive_uri": archive.get("artifact_uri", ""),
        "archive_verified": bool(archive.get("archive_verified")),
    }
    manifest_sha = write_json(manifest_path, manifest)
    return {**manifest, "manifest_path": str(manifest_path), "manifest_sha256": manifest_sha}


def find_sparex_supplier(client: OdooClient) -> int:
    rows = client.call(
        "res.partner",
        "search_read",
        domain=[("name", "=ilike", "Sparex")],
        fields=["id", "name"],
        limit=2,
    )
    if len(rows) != 1:
        raise RuntimeError("Exactly one existing Sparex supplier is required; the pipeline will not create one.")
    return int(rows[0]["id"])


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    client = odoo_client(args.env_file)
    supplier_id = find_sparex_supplier(client)
    product_fields = client.fields("product.template")
    wanted = [
        field
        for field in (
            "id",
            "default_code",
            "name",
            "southern_source_url",
            "seller_ids",
            "website_published",
            "is_published",
        )
        if field in product_fields
    ]
    products = client.call(
        "product.template",
        "search_read",
        domain=[("default_code", "=ilike", "S.%"), ("active", "=", True), ("purchase_ok", "=", True)],
        fields=wanted,
        limit=0,
        order="id",
        context={"active_test": False},
    )
    product_ids = [int(row["id"]) for row in products]
    positive_cost_products: set[int] = set()
    for index in range(0, len(product_ids), 400):
        supplier_rows = client.call(
            "product.supplierinfo",
            "search_read",
            domain=[
                ("product_tmpl_id", "in", product_ids[index : index + 400]),
                ("partner_id", "=", supplier_id),
                ("price", ">", 0),
            ],
            fields=["product_tmpl_id"],
            limit=0,
        )
        positive_cost_products.update(int(row["product_tmpl_id"][0]) for row in supplier_rows if row.get("product_tmpl_id"))

    queue_by_product: dict[int, dict[str, Any]] = {}
    try:
        for index in range(0, len(product_ids), 400):
            queue_rows = client.call(
                "southern.sparex.sourcing.queue",
                "search_read",
                domain=[("product_tmpl_id", "in", product_ids[index : index + 400])],
                fields=["product_tmpl_id", "state", "next_attempt_at", "attempt_count"],
                limit=0,
            )
            for row in queue_rows:
                queue_by_product[int(row["product_tmpl_id"][0])] = row
    except Exception as error:
        if "southern.sparex.sourcing.queue" not in str(error):
            raise
        raise RuntimeError("Deploy or upgrade Southern Parts Intelligence before planning a sourcing run.") from error

    candidates = []
    for product in products:
        product_id = int(product["id"])
        queue = queue_by_product.get(product_id)
        if queue and queue.get("state") in {"manual_review", "rejected", "cost_approved", "cost_applied", "retail_approved", "publication_ready"}:
            continue
        if queue and queue.get("state") == "cooldown" and queue.get("next_attempt_at"):
            continue
        candidates.append(
            {
                "product_id": product_id,
                "sku": clean_sku(product.get("default_code")),
                "source_url": str(product.get("southern_source_url") or ""),
                "supplier_id": supplier_id,
                "has_positive_existing_supplier_price": product_id in positive_cost_products,
                "currently_published": bool(product.get("website_published") or product.get("is_published")),
                "previous_attempts": int((queue or {}).get("attempt_count") or 0),
            }
        )
    candidates.sort(key=lambda row: (not row["has_positive_existing_supplier_price"], not row["currently_published"], row["product_id"]))
    selected = candidates[: args.limit]
    payload = {"requested_limit": args.limit, "eligible_count": len(candidates), "records": selected}
    return write_artifact(
        "plan",
        payload,
        archive_s3=args.archive_s3,
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix,
    )


def load_sparex_session() -> tuple[requests.Session, str]:
    login_url = os.environ.get("SPAREX_DEALER_LOGIN_URL", "").strip()
    products_url = os.environ.get("SPAREX_DEALER_PRODUCTS_URL", "").strip()
    username = os.environ.get("SPAREX_DEALER_USERNAME", "").strip()
    password = os.environ.get("SPAREX_DEALER_PASSWORD", "").strip()
    if not all((login_url, products_url, username, password)):
        raise RuntimeError("Sparex dealer login URL, products URL, username, and password are required.")
    session = requests.Session()
    session.headers.update({"User-Agent": "Titan-Sparex-Sourcing/2.0"})
    first = session.get(login_url, timeout=45)
    first.raise_for_status()
    form_key = ""
    match = re.search(r'name=[\"\']form_key[\"\'][^>]*value=[\"\']([^\"\']+)', first.text, flags=re.I)
    if match:
        form_key = match.group(1)
    payload = {"login[username]": username, "login[password]": password}
    if form_key:
        payload["form_key"] = form_key
    action_match = re.search(r'<form\b[^>]*action=[\"\']([^\"\']+)[\"\'][^>]*>', first.text, flags=re.I)
    post_url = urljoin(login_url, unescape(action_match.group(1))) if action_match else login_url
    response = session.post(post_url, data=payload, timeout=45, allow_redirects=True)
    response.raise_for_status()
    if "customer/account/login" in response.url.casefold():
        raise RuntimeError("Sparex dealer authentication did not establish an account session.")
    return session, products_url.rstrip("/") + "/"


def exact_product_url(session: requests.Session, products_url: str, sku: str, preferred: str) -> str:
    if preferred.startswith("https://us.sparex.com/"):
        return preferred
    search_url = urljoin(products_url, f"catalogsearch/result/?q={quote_plus(sku)}")
    response = session.get(search_url, timeout=45)
    response.raise_for_status()
    digits = re.sub(r"\D", "", sku)
    links = re.findall(r'href=[\"\']([^\"\']*-(?:%s)\.html[^\"\']*)[\"\']' % re.escape(digits), response.text, flags=re.I)
    return urljoin(products_url, unescape(links[0])) if links else ""


def source_plan(args: argparse.Namespace) -> dict[str, Any]:
    plan = verify_explicit_input(args.input, args.input_sha256)
    if plan.get("stage") != "plan":
        raise RuntimeError("Source stage requires an explicit plan artifact.")
    OdooConfig.from_env(args.env_file.resolve())
    dealer_env = args.dealer_env_file
    if dealer_env is None:
        candidate = args.env_file.resolve().parent / "cloud" / "aws" / ".env"
        dealer_env = candidate if candidate.exists() else None
    if dealer_env:
        load_env_file(dealer_env.resolve())
    session, products_url = load_sparex_session()
    outcomes = []
    for record in plan.get("records") or []:
        sku = clean_sku(record.get("sku"))
        outcome = {**record, "status": "not_found", "parser_version": PARSER_VERSION, "retrieved_at_utc": utc_now()}
        try:
            url = exact_product_url(session, products_url, sku, str(record.get("source_url") or ""))
            if not url:
                outcome.update({"failure_code": "not_found", "failure_reason": "No exact Sparex product URL resolved."})
            else:
                response = session.get(url, timeout=45)
                response.raise_for_status()
                if not page_matches_sku(response.text, response.url, sku):
                    outcome.update({"failure_code": "sku_mismatch", "failure_reason": "Resolved page did not prove the exact SKU."})
                else:
                    parsed = choose_exact_price(response.text, sku)
                    outcome.update(
                        {
                            "status": parsed["status"],
                            "evidence_url": response.url,
                            "evidence_sha256": sha256_bytes(response.content),
                            "evidence_schema_version": SCHEMA_VERSION,
                            "price": parsed.get("price", 0.0),
                            "currency": parsed.get("currency", ""),
                            "parse_method": parsed.get("method", ""),
                            "candidate_count": parsed.get("candidate_count", len(parsed.get("candidates") or [])),
                        }
                    )
                    if parsed.get("status") == "accepted" and parsed.get("currency") != "USD":
                        outcome.update(
                            {
                                "status": "currency_review",
                                "failure_code": "currency_review",
                                "failure_reason": "Supplier evidence is not denominated in USD.",
                            }
                        )
                    if parsed["status"] != "accepted":
                        outcome.update({"failure_code": parsed["status"], "failure_reason": "Exact product price was absent or ambiguous."})
        except requests.RequestException as error:
            outcome.update({"status": "request_error", "failure_code": "request_error", "failure_reason": type(error).__name__})
        outcomes.append(outcome)
        time.sleep(args.delay)
    payload = {"input_run_id": plan.get("run_id"), "parser_version": PARSER_VERSION, "records": outcomes}
    return write_artifact(
        "evidence",
        payload,
        input_path=args.input,
        input_sha=args.input_sha256,
        archive_s3=args.archive_s3,
        s3_bucket=args.s3_bucket,
        s3_prefix=args.s3_prefix,
    )


def apply_evidence(args: argparse.Namespace) -> dict[str, Any]:
    evidence = verify_explicit_input(args.input, args.input_sha256)
    if evidence.get("stage") != "evidence":
        raise RuntimeError("Apply-evidence requires an explicit evidence artifact.")
    records = evidence.get("records") or []
    gate = ApplyGate("sparex-stage-evidence", args.apply, args.confirm, args.reason, args.max_records)
    if not args.apply:
        return {"mode": "dry_run", "records": len(records), "accepted": sum(row.get("status") == "accepted" for row in records)}
    gate.authorize(len(records))
    client = odoo_client(args.env_file)
    append_audit(ARTIFACT_ROOT / "write_audit.jsonl", gate.audit_row({"artifact_sha256": args.input_sha256}, len(records)))
    changed = 0
    for record in records:
        values = {
            "supplier_id": record.get("supplier_id"),
            "source_run_id": evidence.get("run_id"),
            "source_artifact_uri": str(args.input),
            "source_input_sha256": args.input_sha256,
            "parser_version": PARSER_VERSION,
            "failure_code": record.get("failure_code"),
            "failure_reason": record.get("failure_reason"),
        }
        if record.get("status") == "accepted":
            values.update(
                {
                    "supplier_price": money(record.get("price")),
                    "evidence_url": record.get("evidence_url"),
                    "evidence_sha256": record.get("evidence_sha256"),
                    "evidence_schema_version": SCHEMA_VERSION,
                    "evidence_retrieved_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                }
            )
        client.call(
            "southern.sparex.sourcing.queue",
            "record_external_attempt",
            product_id=int(record["product_id"]),
            values=values,
        )
        changed += 1
    return {"mode": "apply", "records": len(records), "staged": changed}


def publish_ready(args: argparse.Namespace) -> dict[str, Any]:
    client = odoo_client(args.env_file)
    evidence_cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).replace(tzinfo=None).isoformat()
    rows = client.call(
        "southern.sparex.sourcing.queue",
        "search_read",
        domain=[
            ("state", "=", "publication_ready"),
            ("publication_eligible", "=", True),
            ("evidence_retrieved_at", ">=", evidence_cutoff),
        ],
        fields=["id", "product_tmpl_id", "sku", "approved_retail_price"],
        limit=args.limit,
        order="id",
    )
    ids = [int(row["product_tmpl_id"][0]) for row in rows]
    payload = {"schema_version": SCHEMA_VERSION, "stage": "publish_plan", "created_at_utc": utc_now(), "records": rows}
    if not args.apply:
        return write_artifact(
            "publish_plan",
            payload,
            archive_s3=args.archive_s3,
            s3_bucket=args.s3_bucket,
            s3_prefix=args.s3_prefix,
        )
    gate = ApplyGate("sparex-publish-approved", True, args.confirm, args.reason, args.max_records)
    gate.authorize(len(ids))
    fields_get = client.call("product.template", "fields_get", attributes=["readonly"])
    values = {
        name: True
        for name in ("is_published", "website_published")
        if name in fields_get and not fields_get[name].get("readonly")
    }
    if not values:
        raise RuntimeError("No writable product publication field is available.")
    append_audit(ARTIFACT_ROOT / "write_audit.jsonl", gate.audit_row(ids, len(ids)))
    for index in range(0, len(ids), 100):
        client.call("product.template", "write", ids=ids[index : index + 100], vals=values)
    return {"mode": "apply", "published": len(ids)}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    root.add_argument("--dealer-env-file", type=Path)
    root.add_argument("--archive-s3", action="store_true")
    root.add_argument("--s3-bucket", default=os.environ.get("SOUTHERN_PRODUCT_ARTIFACT_BUCKET", ""))
    root.add_argument("--s3-prefix", default="sparex-sourcing")
    sub = root.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan")
    plan.add_argument("--limit", type=int, default=10)

    source = sub.add_parser("source")
    source.add_argument("--input", type=Path, required=True)
    source.add_argument("--input-sha256", required=True)
    source.add_argument("--delay", type=float, default=1.0)

    stage = sub.add_parser("apply-evidence")
    stage.add_argument("--input", type=Path, required=True)
    stage.add_argument("--input-sha256", required=True)
    stage.add_argument("--apply", action="store_true")
    stage.add_argument("--confirm", default="")
    stage.add_argument("--reason", default="")
    stage.add_argument("--max-records", type=int, default=10)

    publish = sub.add_parser("publish")
    publish.add_argument("--limit", type=int, default=10)
    publish.add_argument("--apply", action="store_true")
    publish.add_argument("--confirm", default="")
    publish.add_argument("--reason", default="")
    publish.add_argument("--max-records", type=int, default=10)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "plan":
        result = build_plan(args)
    elif args.command == "source":
        result = source_plan(args)
    elif args.command == "apply-evidence":
        result = apply_evidence(args)
    elif args.command == "publish":
        result = publish_ready(args)
    else:
        raise RuntimeError("Unsupported command")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
