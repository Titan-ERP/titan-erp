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
BUY_ROUTE_NAME = "Buy"
MTO_ROUTE_NAME = "Replenish on Order (MTO)"


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


def sparex_procurement_values(models, db, uid, api_key, product_fields: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    if "invoice_policy" in product_fields:
        values["invoice_policy"] = "delivery"
    if "route_ids" in product_fields:
        routes = execute(
            models,
            db,
            uid,
            api_key,
            "stock.route",
            "search_read",
            [[("name", "in", [BUY_ROUTE_NAME, MTO_ROUTE_NAME])]],
            {"fields": ["id", "name"], "context": {"active_test": False}, "limit": 10},
        )
        by_name = {route["name"]: route["id"] for route in routes}
        missing = [name for name in [BUY_ROUTE_NAME, MTO_ROUTE_NAME] if name not in by_name]
        if missing:
            raise SystemExit(f"Missing required Sparex route(s): {missing}")
        values["route_ids"] = [(4, by_name[BUY_ROUTE_NAME]), (4, by_name[MTO_ROUTE_NAME])]
    return values


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def clean_sku(value: str) -> str:
    value = (value or "").strip().upper()
    match = re.search(r"S\.\s*(\d+)", value)
    if match:
        return f"S.{match.group(1)}"
    match = re.search(r"-(\d+)\.html", value)
    if match:
        return f"S.{match.group(1)}"
    return value


def title_case_part(value: str) -> str:
    keep_upper = {
        "A/C",
        "AC",
        "BSP",
        "FJIC",
        "ID",
        "JIC",
        "LED",
        "LH",
        "NPT",
        "OD",
        "OEM",
        "ORB",
        "PTO",
        "RH",
        "ROPS",
        "SAE",
        "UNF",
        "UNC",
        "V",
    }
    value = re.sub(r"\s+", " ", (value or "").replace("_", " ")).strip(" -")
    if not value:
        return value
    words = []
    for raw in value.lower().split(" "):
        token = raw.strip()
        upper = token.upper().strip(",")
        if upper in keep_upper:
            words.append(token.replace(upper.lower(), upper))
        elif re.fullmatch(r"s\.\d+", token):
            words.append(token.upper())
        elif re.search(r"\d", token):
            words.append(token)
        else:
            words.append(token.capitalize())
    text = " ".join(words)
    text = text.replace(" Id ", " ID ").replace(" Od ", " OD ")
    text = text.replace(" Oem ", " OEM ").replace(" Pto ", " PTO ")
    return text


def normalized_name(raw_name: str, sku: str) -> str:
    name = title_case_part(raw_name)
    name = re.sub(r"\bFilter, Fuel\b", "Fuel Filter", name, flags=re.IGNORECASE)
    name = re.sub(r"\bFilter, Air\b", "Air Filter", name, flags=re.IGNORECASE)
    name = re.sub(r"\bFilter, Oil\b", "Engine Oil Filter", name, flags=re.IGNORECASE)
    name = re.sub(r"\bFuel Filter Element\b", "Fuel Filter Element", name, flags=re.IGNORECASE)
    name = re.sub(r"\s+-\s+", " - ", name).strip()
    if sku and sku not in name:
        name = f"{name} - Sparex {sku}"
    return name


def record_value(record: dict[str, Any], *path: str, default: Any = "") -> Any:
    value: Any = record
    for part in path:
        if not isinstance(value, dict):
            return default
        value = value.get(part, default)
    return value


def plain_summary(record: dict[str, Any], name: str, sku: str) -> str:
    source_url = record_value(record, "source", "url") or record.get("url", "")
    short_description = record.get("short_description") or record.get("listing_description") or ""
    lines = [
        f"Sparex source: {source_url}",
        f"Product: {name}",
        f"SKU: {sku}",
    ]
    if short_description:
        lines.append(f"Listing note: {short_description}")
    lines.append("Detail enrichment pending: OEM cross references, fitment, catalog pages, and full specifications.")
    return "\n".join(lines).strip()


def html_summary(text: str) -> str:
    return "<pre>" + html.escape(text) + "</pre>"


def rel_id(value: Any) -> int | None:
    if isinstance(value, list) and value:
        return int(value[0])
    if isinstance(value, int):
        return value
    return None


def comparable(value: Any) -> Any:
    if value is False or value is None:
        return ""
    return value


def product_values_match(existing: dict[str, Any], values: dict[str, Any]) -> bool:
    for key, expected in values.items():
        current = existing.get(key)
        if key == "categ_id":
            if rel_id(current) != expected:
                return False
        elif comparable(current) != comparable(expected):
            return False
    return True


def supplier_values_match(existing: dict[str, Any], values: dict[str, Any]) -> bool:
    comparisons = {
        "partner_id": rel_id(existing.get("partner_id")),
        "product_tmpl_id": rel_id(existing.get("product_tmpl_id")),
        "product_code": comparable(existing.get("product_code")),
        "price": float(existing.get("price") or 0.0),
        "delay": int(existing.get("delay") or 0),
        "min_qty": float(existing.get("min_qty") or 0.0),
    }
    expected = {
        "partner_id": values["partner_id"],
        "product_tmpl_id": values["product_tmpl_id"],
        "product_code": comparable(values["product_code"]),
        "price": float(values["price"] or 0.0),
        "delay": int(values["delay"] or 0),
        "min_qty": float(values["min_qty"] or 0.0),
    }
    return comparisons == expected


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
    if not parent_id:
        raise SystemExit(f"Could not create category: {complete_name}")
    return int(parent_id)


def normalize_record(record: dict[str, Any], default_category: str) -> dict[str, Any]:
    product = record.get("product", {})
    source = record.get("source", {})
    sku = clean_sku(product.get("internal_reference") or record.get("internal_reference") or record.get("sku") or source.get("url", ""))
    raw_name = product.get("name") or record.get("name") or record.get("title") or sku
    category = product.get("category") or record.get("category") or default_category
    name = normalized_name(raw_name, sku)
    source_url = source.get("url") or record.get("url") or ""
    vendor = source.get("vendor") or product.get("manufacturer") or "Sparex"
    return {
        "sku": sku,
        "name": name,
        "raw_name": raw_name,
        "category": category,
        "vendor": vendor,
        "source_url": source_url,
        "vendor_code": product.get("vendor_code") or sku,
        "vendor_price": float(product.get("vendor_price") or 0.0),
        "lead_time_days": int(product.get("lead_time_days") or 1),
        "short_description": record.get("short_description") or record.get("listing_description") or "",
        "image_url": record.get("image_url") or product.get("image_url") or "",
        "source_record": record,
    }


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
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 Southern Equipment Odoo product image importer",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
        if not data or (content_type and not content_type.lower().startswith("image/")):
            cache[url] = None
            return None
        encoded = base64.b64encode(data).decode("ascii")
        cache[url] = encoded
        return encoded
    except Exception:
        cache[url] = None
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Fast-import Sparex listing skeletons into Odoo.")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--default-category", default="Parts / Miscellaneous")
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--no-update-existing", action="store_true")
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--progress-every", type=int, default=250)
    args = parser.parse_args()

    socket.setdefaulttimeout(60)
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")

    raw_records = json.loads(args.json_path.read_text(encoding="utf-8"))
    records = [normalize_record(record, args.default_category) for record in raw_records]
    records = [record for record in records if record["sku"]]
    seen = set()
    deduped = []
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
    procurement_values = sparex_procurement_values(models, db, uid, api_key, product_fields)

    category_ids = {category: ensure_category(models, db, uid, api_key, category) for category in sorted({record["category"] for record in records})}
    vendor_id = ensure_partner(models, db, uid, api_key, "Sparex")

    existing_fields = [
        "id",
        "default_code",
        "name",
        "categ_id",
        "sale_ok",
        "purchase_ok",
        "x_studio_manufacturer",
        "description_purchase",
        "description_sale",
        "description",
        "website_description",
    ]
    if "image_1920" in product_fields:
        existing_fields.append("image_1920")
    for optional in ["is_storable", "type"]:
        if optional in product_fields:
            existing_fields.append(optional)

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
                {"fields": existing_fields, "context": {"active_test": False, "bin_size": True}},
            )
        )
    existing_by_sku = {row["default_code"]: row for row in existing_rows}

    values_by_sku: dict[str, dict[str, Any]] = {}
    for record in records:
        summary = plain_summary(record["source_record"], record["name"], record["sku"])
        values: dict[str, Any] = {
            "default_code": record["sku"],
            "name": record["name"],
            "categ_id": category_ids[record["category"]],
            "sale_ok": True,
            "purchase_ok": True,
            "x_studio_manufacturer": "Sparex",
            "description_purchase": summary,
            "description_sale": summary,
            "description": html_summary(summary),
            "website_description": html_summary(summary),
        }
        if "is_storable" in product_fields:
            values["is_storable"] = True
        elif "type" in product_fields:
            values["type"] = "product"
        values.update(procurement_values)
        values_by_sku[record["sku"]] = values

    image_cache: dict[str, str | None] = {}
    image_status_by_sku: dict[str, str] = {}
    if "image_1920" in product_fields and not args.skip_images:
        for record in records:
            if record["sku"] in existing_by_sku and has_binary(existing_by_sku[record["sku"]].get("image_1920")):
                image_status_by_sku[record["sku"]] = "Already Present"
                continue
            image_data = download_image_b64(record.get("image_url", ""), image_cache)
            if image_data:
                values_by_sku[record["sku"]]["image_1920"] = image_data
                image_status_by_sku[record["sku"]] = "Loaded"
            elif record.get("image_url"):
                image_status_by_sku[record["sku"]] = "Download Failed"
            else:
                image_status_by_sku[record["sku"]] = "No Source Image"

    results = []
    new_records = [record for record in records if record["sku"] not in existing_by_sku]
    processed = 0
    for record_chunk in chunks(new_records, args.chunk_size):
        created_ids = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "create",
            [[values_by_sku[record["sku"]] for record in record_chunk]],
        )
        if isinstance(created_ids, int):
            created_ids = [created_ids]
        for record, product_id in zip(record_chunk, created_ids):
            existing_by_sku[record["sku"]] = {"id": product_id, "default_code": record["sku"]}
            results.append((record, product_id, "Created"))
        processed += len(record_chunk)
        if args.progress_every and processed % args.progress_every == 0:
            print(f"Created phase: {processed}/{len(new_records)}", flush=True)

    created_skus = {record["sku"] for record, _, status in results if status == "Created"}
    if not args.no_update_existing:
        update_processed = 0
        for record in records:
            if record["sku"] in created_skus:
                continue
            existing = existing_by_sku[record["sku"]]
            product_id = existing["id"]
            if product_values_match(existing, values_by_sku[record["sku"]]):
                results.append((record, product_id, "Skipped"))
            else:
                execute(models, db, uid, api_key, "product.template", "write", [[product_id], values_by_sku[record["sku"]]])
                results.append((record, product_id, "Updated"))
            update_processed += 1
            if args.progress_every and update_processed % args.progress_every == 0:
                created = sum(1 for _, _, status in results if status == "Created")
                updated = sum(1 for _, _, status in results if status == "Updated")
                skipped = sum(1 for _, _, status in results if status == "Skipped")
                print(
                    f"Update phase: {update_processed}/{len(records) - len(created_skus)} | "
                    f"created={created} updated={updated} skipped={skipped}",
                    flush=True,
                )
    else:
        for record in records:
            if record["sku"] not in created_skus:
                results.append((record, existing_by_sku[record["sku"]]["id"], "Existing"))

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
        supplier_values = {
            "partner_id": vendor_id,
            "product_tmpl_id": product_id,
            "product_code": record["vendor_code"],
            "price": record["vendor_price"],
            "delay": record["lead_time_days"],
            "min_qty": 1,
        }
        if product_id in supplier_by_product:
            existing_supplier = supplier_by_product[product_id]
            if not supplier_values_match(existing_supplier, supplier_values):
                execute(models, db, uid, api_key, "product.supplierinfo", "write", [[existing_supplier["id"]], supplier_values])
        else:
            supplier_creates.append(supplier_values)
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
                {"fields": ["id", "default_code", "name", "categ_id", "seller_ids"], "context": {"active_test": False}},
            )
        )
    verify_by_sku = {row["default_code"]: row for row in verify_rows}

    result_path = args.json_path.with_name(f"{args.json_path.stem}_fast_odoo_results.csv")
    with result_path.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["Timestamp", "Status", "Verified", "Product ID", "Internal Reference", "Name", "Category", "Image Status", "Source URL"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record, product_id, status in results:
            verified = record["sku"] in verify_by_sku and bool(verify_by_sku[record["sku"]].get("seller_ids"))
            writer.writerow(
                {
                    "Timestamp": datetime.now().isoformat(timespec="seconds"),
                    "Status": status,
                    "Verified": "Yes" if verified else "No",
                    "Product ID": product_id,
                    "Internal Reference": record["sku"],
                    "Name": record["name"],
                    "Category": record["category"],
                    "Image Status": image_status_by_sku.get(record["sku"], "Not Checked"),
                    "Source URL": record["source_url"],
                }
            )

    created = sum(1 for _, _, status in results if status == "Created")
    updated = sum(1 for _, _, status in results if status == "Updated")
    skipped = sum(1 for _, _, status in results if status == "Skipped")
    images_loaded = sum(1 for status in image_status_by_sku.values() if status == "Loaded")
    verified = sum(1 for record, _, _ in results if record["sku"] in verify_by_sku and bool(verify_by_sku[record["sku"]].get("seller_ids")))
    print(f"JSON source: {args.json_path}")
    print(f"Results: {result_path}")
    print(f"Rows: {len(results)}")
    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Skipped unchanged: {skipped}")
    print(f"Images loaded: {images_loaded}")
    print(f"Verified: {verified}/{len(results)}")
    if verified != len(results):
        raise SystemExit("Some fast Sparex imports failed verification.")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
