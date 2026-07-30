from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}.")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    load_env()
    socket.setdefaulttimeout(float(os.environ.get("ODOO_XMLRPC_TIMEOUT", "30")))
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def clean_sku(value: str) -> str:
    value = (value or "").strip().upper()
    match = re.search(r"S\.\s*(\d+)", value)
    if match:
        return f"S.{match.group(1)}"
    return value


def as_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("products", "records", "items"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        return [payload]
    return []


def first_value(record: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = record
        ok = True
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                ok = False
                break
            value = value[part]
        if ok and value not in (None, ""):
            return value
    return ""


def ensure_model_installed(models, db, uid, api_key) -> None:
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.model",
        "search_read",
        [[("model", "=", "southern.parts.oem_reference")]],
        {"fields": ["id"], "limit": 1},
    )
    if not rows:
        raise SystemExit("southern_parts_intelligence is not installed in Odoo yet.")


def find_product(models, db, uid, api_key, sku: str) -> int | None:
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_read",
        [[("default_code", "=", sku)]],
        {"fields": ["id"], "limit": 1},
    )
    return rows[0]["id"] if rows else None


def find_or_create(models, db, uid, api_key, model: str, domain: list[Any], values: dict[str, Any], apply: bool) -> tuple[str, int | None]:
    rows = execute(models, db, uid, api_key, model, "search_read", [domain], {"fields": ["id"], "limit": 1})
    if rows:
        return "existing", rows[0]["id"]
    if not apply:
        return "would_create", None
    return "created", execute(models, db, uid, api_key, model, "create", [values])


def ensure_make_model(models, db, uid, api_key, make_name: str, model_name: str, apply: bool) -> tuple[int | None, int | None, str]:
    make_name = str(make_name or "").strip()
    model_name = str(model_name or "").strip()
    if not make_name or not model_name:
        return None, None, "missing_make_or_model"
    make_status, make_id = find_or_create(
        models,
        db,
        uid,
        api_key,
        "southern.parts.make",
        [("name", "=", make_name)],
        {"name": make_name},
        apply,
    )
    if make_id is None:
        return None, None, make_status
    model_status, model_id = find_or_create(
        models,
        db,
        uid,
        api_key,
        "southern.parts.model",
        [("make_id", "=", make_id), ("name", "=", model_name)],
        {"make_id": make_id, "name": model_name},
        apply,
    )
    return make_id, model_id, model_status


def list_value(record: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    for key in keys:
        value = first_value(record, key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def import_record(models, db, uid, api_key, record: dict[str, Any], apply: bool) -> dict[str, Any]:
    sku = clean_sku(str(first_value(record, "product.internal_reference", "internal_reference", "sku", "default_code")))
    source = first_value(record, "source.vendor", "source_name") or "Unknown"
    source_url = first_value(record, "source.url", "source_url")
    if not sku:
        return {"sku": "", "status": "skipped", "reason": "missing_sku"}

    product_id = find_product(models, db, uid, api_key, sku)
    if not product_id:
        return {"sku": sku, "status": "skipped", "reason": "product_not_found"}

    counts = {"specs": 0, "fitments": 0, "oem_refs": 0, "catalog_pages": 0, "related_parts": 0, "barcodes": 0}
    enrichment_status = str(record.get("enrichment_status") or "complete").strip() or "complete"
    if enrichment_status not in {"none", "partial", "complete", "review"}:
        enrichment_status = "review"
    product_update = {
        "southern_source_name": source,
        "southern_source_url": source_url,
        "southern_enrichment_status": enrichment_status,
    }
    if apply:
        execute(models, db, uid, api_key, "product.template", "write", [[product_id], product_update])

    for spec in list_value(record, "specifications", "product_specifications", "specs"):
        name = str(spec.get("name") or spec.get("label") or "").strip()
        value = str(spec.get("value") or "").strip()
        if not name or not value:
            continue
        values = {
            "product_tmpl_id": product_id,
            "group_name": spec.get("group_name") or spec.get("group") or "Specifications",
            "name": name,
            "value": value,
            "unit": spec.get("unit") or "",
            "source_name": spec.get("source_name") or source,
            "source_url": spec.get("source_url") or source_url,
            "confidence": float(spec.get("confidence") or 1.0),
        }
        status, _ = find_or_create(
            models,
            db,
            uid,
            api_key,
            "southern.parts.specification",
            [("product_tmpl_id", "=", product_id), ("name", "=", name), ("value", "=", value)],
            values,
            apply,
        )
        if status != "existing":
            counts["specs"] += 1

    for ref in list_value(record, "oem_references", "oem_part_numbers", "oem_refs"):
        manufacturer = str(ref.get("manufacturer") or ref.get("make") or "").strip()
        part_number = str(ref.get("part_number") or ref.get("oem_part_number") or ref.get("value") or "").strip()
        if not manufacturer or not part_number:
            continue
        values = {
            "product_tmpl_id": product_id,
            "manufacturer": manufacturer,
            "oem_part_number": part_number,
            "reference_type": ref.get("reference_type") or "oem",
            "source_name": ref.get("source_name") or source,
            "source_url": ref.get("source_url") or source_url,
            "confidence": float(ref.get("confidence") or 1.0),
        }
        status, _ = find_or_create(
            models,
            db,
            uid,
            api_key,
            "southern.parts.oem_reference",
            [
                ("product_tmpl_id", "=", product_id),
                ("manufacturer", "=", manufacturer),
                ("oem_part_number", "=", part_number),
            ],
            values,
            apply,
        )
        if status != "existing":
            counts["oem_refs"] += 1

    for fitment in list_value(record, "fitments", "suitable_for"):
        make_id, model_id, status = ensure_make_model(models, db, uid, api_key, fitment.get("make"), fitment.get("model"), apply)
        if not make_id or not model_id:
            continue
        values = {
            "product_tmpl_id": product_id,
            "make_id": make_id,
            "model_id": model_id,
            "engine": fitment.get("engine") or "",
            "year_from": fitment.get("year_from") or 0,
            "year_to": fitment.get("year_to") or 0,
            "build_list": fitment.get("build_list") or "",
            "notes": fitment.get("notes") or "",
            "source_name": fitment.get("source_name") or source,
            "source_url": fitment.get("source_url") or source_url,
            "confidence": float(fitment.get("confidence") or 1.0),
        }
        status, _ = find_or_create(
            models,
            db,
            uid,
            api_key,
            "southern.parts.fitment",
            [("product_tmpl_id", "=", product_id), ("make_id", "=", make_id), ("model_id", "=", model_id)],
            values,
            apply,
        )
        if status != "existing":
            counts["fitments"] += 1

    for catalog in list_value(record, "catalog_pages", "catalogs"):
        catalog_name = str(catalog.get("catalog_name") or catalog.get("catalog") or "").strip()
        page_number = str(catalog.get("page_number") or catalog.get("page") or "").strip()
        if not catalog_name or not page_number:
            continue
        values = {
            "product_tmpl_id": product_id,
            "catalog_code": catalog.get("catalog_code") or "",
            "catalog_name": catalog_name,
            "page_number": page_number,
            "source_name": catalog.get("source_name") or source,
            "source_url": catalog.get("source_url") or source_url,
        }
        status, _ = find_or_create(
            models,
            db,
            uid,
            api_key,
            "southern.parts.catalog_page",
            [("product_tmpl_id", "=", product_id), ("catalog_name", "=", catalog_name), ("page_number", "=", page_number)],
            values,
            apply,
        )
        if status != "existing":
            counts["catalog_pages"] += 1

    for related in list_value(record, "related_parts", "related_products"):
        related_sku = clean_sku(str(related.get("internal_reference") or related.get("sku") or related.get("default_code") or ""))
        if not related_sku:
            continue
        related_product_id = find_product(models, db, uid, api_key, related_sku)
        if not related_product_id:
            continue
        relationship_type = related.get("relationship_type") or "related"
        values = {
            "product_tmpl_id": product_id,
            "related_product_tmpl_id": related_product_id,
            "relationship_type": relationship_type,
            "source_name": related.get("source_name") or source,
            "source_url": related.get("source_url") or source_url,
            "confidence": float(related.get("confidence") or 1.0),
            "notes": related.get("notes") or "",
        }
        status, _ = find_or_create(
            models,
            db,
            uid,
            api_key,
            "southern.parts.related_product",
            [
                ("product_tmpl_id", "=", product_id),
                ("related_product_tmpl_id", "=", related_product_id),
                ("relationship_type", "=", relationship_type),
            ],
            values,
            apply,
        )
        if status != "existing":
            counts["related_parts"] += 1

    for barcode in list_value(record, "alternate_barcodes", "barcodes"):
        code = str(barcode.get("barcode") or barcode.get("value") or "").strip()
        if not code:
            continue
        values = {
            "product_tmpl_id": product_id,
            "barcode": code,
            "barcode_type": barcode.get("barcode_type") or barcode.get("type") or "other",
            "source_name": barcode.get("source_name") or source,
            "source_url": barcode.get("source_url") or source_url,
        }
        status, _ = find_or_create(
            models,
            db,
            uid,
            api_key,
            "southern.parts.alternate_barcode",
            [("product_tmpl_id", "=", product_id), ("barcode", "=", code)],
            values,
            apply,
        )
        if status != "existing":
            counts["barcodes"] += 1

    return {"sku": sku, "status": "applied" if apply else "dry_run", **counts}


def count_record_candidates(models, db, uid, api_key, record: dict[str, Any]) -> dict[str, Any]:
    sku = clean_sku(str(first_value(record, "product.internal_reference", "internal_reference", "sku", "default_code")))
    if not sku:
        return {"sku": "", "status": "skipped", "reason": "missing_sku"}
    product_id = find_product(models, db, uid, api_key, sku)
    if not product_id:
        return {"sku": sku, "status": "skipped", "reason": "product_not_found"}

    counts = {
        "specs": 0,
        "fitments": 0,
        "oem_refs": 0,
        "catalog_pages": 0,
        "related_parts": 0,
        "barcodes": 0,
    }
    for spec in list_value(record, "specifications", "product_specifications", "specs"):
        if str(spec.get("name") or spec.get("label") or "").strip() and str(spec.get("value") or "").strip():
            counts["specs"] += 1
    for ref in list_value(record, "oem_references", "oem_part_numbers", "oem_refs"):
        if str(ref.get("manufacturer") or ref.get("make") or "").strip() and str(
            ref.get("part_number") or ref.get("oem_part_number") or ref.get("value") or ""
        ).strip():
            counts["oem_refs"] += 1
    for fitment in list_value(record, "fitments", "suitable_for"):
        if str(fitment.get("make") or "").strip() and str(fitment.get("model") or "").strip():
            counts["fitments"] += 1
    for catalog in list_value(record, "catalog_pages", "catalogs"):
        if str(catalog.get("catalog_name") or catalog.get("catalog") or "").strip() and str(
            catalog.get("page_number") or catalog.get("page") or ""
        ).strip():
            counts["catalog_pages"] += 1
    for related in list_value(record, "related_parts", "related_products"):
        if clean_sku(str(related.get("internal_reference") or related.get("sku") or related.get("default_code") or "")):
            counts["related_parts"] += 1
    for barcode in list_value(record, "alternate_barcodes", "barcodes"):
        if str(barcode.get("barcode") or barcode.get("value") or "").strip():
            counts["barcodes"] += 1

    return {"sku": sku, "status": "preflight", "product_id": product_id, **counts}


def main() -> None:
    parser = argparse.ArgumentParser(description="Import Sparex/Blumaq-style parts intelligence detail JSON into Odoo.")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--apply", action="store_true", help="Write changes to Odoo. Without this flag, runs read-only.")
    parser.add_argument("--preflight", action="store_true", help="Read-only fast candidate count without per-child Odoo lookup or writes.")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N records before applying --limit.")
    parser.add_argument("--limit", type=int, default=0, help="Process only the first N records after loading the payload.")
    parser.add_argument("--progress-every", type=int, default=1, help="Print progress every N records. Use 0 to disable.")
    args = parser.parse_args()

    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    records = as_records(payload)
    if args.offset:
        records = records[args.offset :]
    if args.limit:
        records = records[: args.limit]
    db, uid, api_key, models = connect()
    ensure_model_installed(models, db, uid, api_key)

    totals: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        if args.preflight:
            result = count_record_candidates(models, db, uid, api_key, record)
        else:
            result = import_record(models, db, uid, api_key, record, args.apply)
        results.append(result)
        for key, value in result.items():
            if isinstance(value, int) and key != "product_id":
                totals[key] = totals.get(key, 0) + value
        if result.get("status") == "skipped":
            totals[f"skipped_{result.get('reason')}"] = totals.get(f"skipped_{result.get('reason')}", 0) + 1
        if args.progress_every and (index == 1 or index % args.progress_every == 0 or index == len(records)):
            print(
                json.dumps(
                    {
                        "progress": index,
                        "records": len(records),
                        "sku": result.get("sku", ""),
                        "status": result.get("status", ""),
                    }
                ),
                flush=True,
            )

    mode = "preflight" if args.preflight else "apply" if args.apply else "dry_run"
    print(json.dumps({"records": len(records), "mode": mode, "totals": totals, "results": results}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)

