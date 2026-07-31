from __future__ import annotations

import argparse
import base64
import csv
import html
import json
import os
import re
import socket
import sys
import urllib.request
import xmlrpc.client
from datetime import datetime
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
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def clean_ref(value: str) -> str:
    value = re.sub(r"\s+", "", (value or "").strip().upper())
    if value.startswith("BLQ-"):
        return value
    return f"BLQ-{value}" if value else ""


def clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "")).strip()


def title_case_part(value: str) -> str:
    keep_upper = {"A/C", "AC", "BQ", "CAT", "ID", "LH", "OEM", "OD", "PTO", "RH", "SAE"}
    words = []
    for token in clean_text(value).lower().split(" "):
        upper = token.upper().strip(",")
        if upper in keep_upper or re.search(r"\d", token):
            words.append(token.upper() if upper in keep_upper else token)
        else:
            words.append(token.capitalize())
    return " ".join(words)


def normalized_name(raw_name: str, supplier_sku: str) -> str:
    name = title_case_part(raw_name)
    replacements = {
        r"\bFilter Suitable\b": "Filter Suitable",
        r"\bHydraulic Element\b": "Hydraulic Element",
        r"\bCap Assy\.?\b": "Cap Assembly",
    }
    for pattern, replacement in replacements.items():
        name = re.sub(pattern, replacement, name, flags=re.I)
    return clean_text(f"{name} - Blumaq {supplier_sku}") if supplier_sku and supplier_sku not in name else clean_text(name)


def html_summary(text: str) -> str:
    return "<pre>" + html.escape(text or "") + "</pre>"


def rel_id(value: Any) -> int | None:
    if isinstance(value, list) and value:
        return int(value[0])
    if isinstance(value, int):
        return value
    return None


def ensure_partner(models, db, uid, api_key, name: str) -> int:
    rows = execute(models, db, uid, api_key, "res.partner", "search_read", [[("name", "=", name)]], {"fields": ["id"], "limit": 1})
    if rows:
        return rows[0]["id"]
    return execute(models, db, uid, api_key, "res.partner", "create", [{"name": name, "supplier_rank": 1, "company_type": "company"}])


def ensure_category(models, db, uid, api_key, complete_name: str) -> int:
    complete_name = re.sub(r"\s*/\s*", " / ", complete_name.strip())
    rows = execute(models, db, uid, api_key, "product.category", "search_read", [[("complete_name", "=", complete_name)]], {"fields": ["id"], "limit": 1})
    if rows:
        return rows[0]["id"]

    parent_id = False
    path = []
    for part in [piece.strip() for piece in complete_name.split("/") if piece.strip()]:
        path.append(part)
        partial = " / ".join(path)
        rows = execute(models, db, uid, api_key, "product.category", "search_read", [[("complete_name", "=", partial)]], {"fields": ["id"], "limit": 1})
        if rows:
            parent_id = rows[0]["id"]
            continue
        values: dict[str, Any] = {"name": part}
        if parent_id:
            values["parent_id"] = parent_id
        parent_id = execute(models, db, uid, api_key, "product.category", "create", [values])
    return int(parent_id)


def has_binary(value: Any) -> bool:
    if value in (False, None, ""):
        return False
    if isinstance(value, str):
        return value not in {"0", "False", "false"}
    return bool(value)


def download_image_b64(url: str, cache: dict[str, str | None]) -> str | None:
    if not url:
        return None
    if url in cache:
        return cache[url]
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Southern Equipment Blumaq image importer"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
        if not data or (content_type and not content_type.lower().startswith("image/")):
            cache[url] = None
            return None
        cache[url] = base64.b64encode(data).decode("ascii")
        return cache[url]
    except Exception:
        cache[url] = None
        return None


def normalize_record(record: dict[str, Any], default_category: str) -> dict[str, Any]:
    product = record.get("product", {})
    source = record.get("source", {})
    supplier_sku = clean_text(product.get("supplier_sku") or product.get("vendor_code") or product.get("internal_reference") or "")
    internal_ref = clean_ref(product.get("internal_reference") or supplier_sku)
    name = normalized_name(product.get("name") or record.get("name") or supplier_sku, supplier_sku)
    description = record.get("description") or f"Blumaq source: {source.get('url', '')}\nReference: {supplier_sku}\nPricing/cost pending review."
    return {
        "sku": internal_ref,
        "supplier_sku": supplier_sku,
        "name": name,
        "category": product.get("category") or record.get("category") or default_category,
        "source_url": source.get("url") or record.get("url") or "",
        "vendor_code": supplier_sku or internal_ref,
        "vendor_price": float(product.get("vendor_price") or 0.0),
        "lead_time_days": int(product.get("lead_time_days") or 3),
        "description": description,
        "image_url": record.get("image_url") or product.get("image_url") or "",
        "source_record": record,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Import public Blumaq skeleton JSON into Odoo with safe BLQ-prefixed internal references.")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--default-category", default="Parts / Miscellaneous")
    parser.add_argument("--chunk-size", type=int, default=25)
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    socket.setdefaulttimeout(60)
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")

    raw_records = json.loads(args.json_path.read_text(encoding="utf-8"))
    records = [normalize_record(record, args.default_category) for record in raw_records]
    records = [record for record in records if record["sku"] and record["source_url"]]
    deduped = []
    seen = set()
    for record in records:
        if record["sku"] in seen:
            continue
        seen.add(record["sku"])
        deduped.append(record)
    records = deduped

    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})

    existing_rows = []
    for sku_chunk in chunks([record["sku"] for record in records], 300):
        existing_rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "search_read",
                [[("default_code", "in", sku_chunk)]],
                {"fields": ["id", "default_code", "name", "image_1920"] if "image_1920" in product_fields else ["id", "default_code", "name"], "context": {"active_test": False, "bin_size": True}},
            )
        )
    existing_by_sku = {row["default_code"]: row for row in existing_rows}

    if args.dry_run:
        print(f"Dry run records: {len(records)}")
        print(f"Existing exact BLQ references: {len(existing_by_sku)}")
        print(f"Would create: {len([record for record in records if record['sku'] not in existing_by_sku])}")
        print(f"Would update: {len([record for record in records if record['sku'] in existing_by_sku])}")
        return

    category_ids = {category: ensure_category(models, db, uid, api_key, category) for category in sorted({record["category"] for record in records})}
    vendor_id = ensure_partner(models, db, uid, api_key, "Blumaq")

    image_cache: dict[str, str | None] = {}
    image_status: dict[str, str] = {}
    values_by_sku: dict[str, dict[str, Any]] = {}
    for record in records:
        values: dict[str, Any] = {
            "default_code": record["sku"],
            "name": record["name"],
            "categ_id": category_ids[record["category"]],
            "sale_ok": True,
            "purchase_ok": True,
            "x_studio_manufacturer": "Blumaq",
            "description_purchase": record["description"],
            "description_sale": record["description"],
            "description": html_summary(record["description"]),
            "website_description": html_summary(record["description"]),
        }
        if "is_storable" in product_fields:
            values["is_storable"] = True
        elif "type" in product_fields:
            values["type"] = "product"
        if "image_1920" in product_fields and not args.skip_images:
            existing = existing_by_sku.get(record["sku"], {})
            if has_binary(existing.get("image_1920")):
                image_status[record["sku"]] = "Already Present"
            else:
                data = download_image_b64(record["image_url"], image_cache)
                if data:
                    values["image_1920"] = data
                    image_status[record["sku"]] = "Loaded"
                elif record["image_url"]:
                    image_status[record["sku"]] = "Download Failed"
                else:
                    image_status[record["sku"]] = "No Source Image"
        values_by_sku[record["sku"]] = values

    results = []
    for record in records:
        if record["sku"] in existing_by_sku:
            product_id = existing_by_sku[record["sku"]]["id"]
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], values_by_sku[record["sku"]]])
            results.append((record, product_id, "Updated"))
        else:
            product_id = execute(models, db, uid, api_key, "product.template", "create", [values_by_sku[record["sku"]]])
            results.append((record, product_id, "Created"))

    supplier_existing = []
    product_ids = [product_id for _, product_id, _ in results]
    for id_chunk in chunks(product_ids, 300):
        supplier_existing.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.supplierinfo",
                "search_read",
                [[("partner_id", "=", vendor_id), ("product_tmpl_id", "in", id_chunk)]],
                {"fields": ["id", "partner_id", "product_tmpl_id", "product_code", "price", "delay", "min_qty"]},
            )
        )
    supplier_by_product = {row["product_tmpl_id"][0]: row for row in supplier_existing if row.get("product_tmpl_id")}
    supplier_creates = []
    for record, product_id, _ in results:
        values = {
            "partner_id": vendor_id,
            "product_tmpl_id": product_id,
            "product_code": record["vendor_code"],
            "price": record["vendor_price"],
            "delay": record["lead_time_days"],
            "min_qty": 1,
        }
        if product_id in supplier_by_product:
            execute(models, db, uid, api_key, "product.supplierinfo", "write", [[supplier_by_product[product_id]["id"]], values])
        else:
            supplier_creates.append(values)
    for supplier_chunk in chunks(supplier_creates, args.chunk_size):
        execute(models, db, uid, api_key, "product.supplierinfo", "create", [supplier_chunk])

    verify_rows = []
    for sku_chunk in chunks([record["sku"] for record, _, _ in results], 300):
        verify_rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "search_read",
                [[("default_code", "in", sku_chunk)]],
                {"fields": ["id", "default_code", "name", "seller_ids"], "context": {"active_test": False}},
            )
        )
    verify_by_sku = {row["default_code"]: row for row in verify_rows}
    result_path = args.json_path.with_name(f"{args.json_path.stem}_odoo_results.csv")
    with result_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["Timestamp", "Status", "Verified", "Product ID", "Internal Reference", "Supplier SKU", "Name", "Category", "Image Status", "Source URL"])
        writer.writeheader()
        for record, product_id, status in results:
            writer.writerow(
                {
                    "Timestamp": datetime.now().isoformat(timespec="seconds"),
                    "Status": status,
                    "Verified": "Yes" if record["sku"] in verify_by_sku and verify_by_sku[record["sku"]].get("seller_ids") else "No",
                    "Product ID": product_id,
                    "Internal Reference": record["sku"],
                    "Supplier SKU": record["supplier_sku"],
                    "Name": record["name"],
                    "Category": record["category"],
                    "Image Status": image_status.get(record["sku"], "Not Checked"),
                    "Source URL": record["source_url"],
                }
            )
    verified = sum(1 for record, _, _ in results if record["sku"] in verify_by_sku and verify_by_sku[record["sku"]].get("seller_ids"))
    print(f"JSON source: {args.json_path}")
    print(f"Results: {result_path}")
    print(f"Rows: {len(results)}")
    print(f"Created: {sum(1 for _, _, status in results if status == 'Created')}")
    print(f"Updated: {sum(1 for _, _, status in results if status == 'Updated')}")
    print(f"Images loaded: {sum(1 for status in image_status.values() if status == 'Loaded')}")
    print(f"Verified: {verified}/{len(results)}")
    if verified != len(results):
        raise SystemExit("Some Blumaq imports failed verification.")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
