from __future__ import annotations

import argparse
import csv
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "backlog"


def load_env() -> None:
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    socket.setdefaulttimeout(90)
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
        except (OSError, TimeoutError, xmlrpc.client.ProtocolError) as exc:
            last_exc = exc
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise last_exc or RuntimeError("Unknown Odoo XML-RPC failure")


def chunks(values: list[Any], size: int):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def has_binary(value: Any) -> bool:
    if value in (False, None, ""):
        return False
    if isinstance(value, str):
        return value not in {"0", "False", "false"}
    return bool(value)


def text(value: Any) -> str:
    if value in (False, None):
        return ""
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return str(value[1]).strip()
    return str(value).strip()


def classify(product: dict[str, Any], description_fields: list[str]) -> tuple[str, str, list[str]]:
    sku = text(product.get("default_code"))
    active = bool(product.get("active"))
    sale_ok = bool(product.get("sale_ok"))
    cost = float(product.get("standard_price") or 0.0)
    retail = float(product.get("list_price") or 0.0)
    source_url = text(product.get("southern_source_url"))
    source_name = text(product.get("southern_source_name"))
    seller_count = len(product.get("seller_ids") or [])
    has_category = bool(product.get("public_categ_ids"))
    has_image = has_binary(product.get("image_1920"))
    descriptions = [text(product.get(field)) for field in description_fields]
    has_description = any(descriptions)
    internal_copy = any(
        marker.lower() in description.lower()
        for description in descriptions
        for marker in (
            "detail enrichment pending",
            "pricing requires separate review",
            "public blumaq page harvested",
        )
    )
    published = bool(product.get("website_published")) or bool(product.get("is_published"))

    needs: list[str] = []
    if not sku.upper().startswith("S."):
        needs.append("not_sparex_sku")
    if not active:
        needs.append("inactive")
    if not sale_ok:
        needs.append("not_saleable")
    if cost <= 0:
        needs.append("missing_verified_dealer_cost")
    if not source_url:
        needs.append("missing_sparex_source_url")
    if not source_name:
        needs.append("missing_source_name")
    if seller_count == 0:
        needs.append("missing_supplierinfo")
    if retail <= 1:
        needs.append("missing_or_placeholder_sales_price")
    elif cost > 0 and retail <= cost:
        needs.append("sales_price_not_above_cost")
    if not has_category:
        needs.append("missing_website_category")
    if not has_image:
        needs.append("missing_image")
    if not has_description or internal_copy:
        needs.append("missing_customer_description")

    if "missing_verified_dealer_cost" in needs or "missing_sparex_source_url" in needs or "missing_supplierinfo" in needs:
        bucket = "P0 Dealer Update Needed"
    elif "missing_or_placeholder_sales_price" in needs or "sales_price_not_above_cost" in needs:
        bucket = "P1 Retail Price Needed"
    elif not published and active and sale_ok and retail > 1 and cost > 0 and has_category and has_image and has_description and not internal_copy:
        bucket = "P2 Website Ready To Publish"
    elif not published:
        bucket = "P3 Website Content Needed"
    else:
        bucket = "OK Published Or No Action"

    return bucket, "; ".join(needs), needs


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a read-only backlog of existing Sparex products needing updates.")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    description_fields = [field for field in ("description_ecommerce", "website_description", "description_sale") if field in fields_get]
    optional_fields = [
        "is_published",
        "website_published",
        "website_url",
        "public_categ_ids",
        "seller_ids",
        "image_1920",
        "southern_source_url",
        "southern_source_name",
        *description_fields,
    ]
    wanted = [
        "id",
        "default_code",
        "name",
        "active",
        "sale_ok",
        "standard_price",
        "list_price",
        "categ_id",
        *[field for field in optional_fields if field in fields_get],
    ]
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [[("default_code", "=like", "S.%")]],
        {"context": {"active_test": False}, "order": "id asc", "limit": args.limit or 0},
    )

    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for id_chunk in chunks(product_ids, 250):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [id_chunk],
            {"fields": wanted, "context": {"active_test": False, "bin_size": True}},
        )
        for product in products:
            bucket, needs_text, needs = classify(product, description_fields)
            counts[bucket] = counts.get(bucket, 0) + 1
            rows.append(
                {
                    "Priority Bucket": bucket,
                    "Product ID": product.get("id"),
                    "Internal Reference": text(product.get("default_code")),
                    "Name": text(product.get("name")),
                    "Active": product.get("active"),
                    "Sale OK": product.get("sale_ok"),
                    "Cost": f"{float(product.get('standard_price') or 0.0):.2f}",
                    "Sales Price": f"{float(product.get('list_price') or 0.0):.2f}",
                    "Category": text(product.get("categ_id")),
                    "Source Name": text(product.get("southern_source_name")),
                    "Source URL": text(product.get("southern_source_url")),
                    "Supplierinfo Count": len(product.get("seller_ids") or []),
                    "Has Website Category": "yes" if product.get("public_categ_ids") else "no",
                    "Has Image": "yes" if has_binary(product.get("image_1920")) else "no",
                    "Has Customer Description": "yes" if any(text(product.get(field)) for field in description_fields) else "no",
                    "Website Published": product.get("website_published", ""),
                    "Is Published": product.get("is_published", ""),
                    "Website URL": text(product.get("website_url")),
                    "Needs": needs_text,
                    "Needs Count": len(needs),
                }
            )

    priority_order = {
        "P0 Dealer Update Needed": 0,
        "P1 Retail Price Needed": 1,
        "P2 Website Ready To Publish": 2,
        "P3 Website Content Needed": 3,
        "OK Published Or No Action": 4,
    }
    rows.sort(key=lambda row: (priority_order.get(row["Priority Bucket"], 99), int(row["Product ID"] or 0)))

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_name = args.run_name or f"sparex_update_backlog_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_path = OUT_DIR / f"{run_name}.csv"
    fieldnames = [
        "Priority Bucket",
        "Product ID",
        "Internal Reference",
        "Name",
        "Active",
        "Sale OK",
        "Cost",
        "Sales Price",
        "Category",
        "Source Name",
        "Source URL",
        "Supplierinfo Count",
        "Has Website Category",
        "Has Image",
        "Has Customer Description",
        "Website Published",
        "Is Published",
        "Website URL",
        "Needs",
        "Needs Count",
    ]
    with out_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {out_path}")
    print(f"Products observed: {len(rows)}")
    for bucket in sorted(counts, key=lambda key: priority_order.get(key, 99)):
        print(f"{bucket}: {counts[bucket]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
