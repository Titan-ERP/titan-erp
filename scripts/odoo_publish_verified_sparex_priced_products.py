"""Publish Sparex products from a verified price evidence report.

This is intentionally narrower than the general storefront publisher. It only
publishes active, saleable Sparex products whose SKU appears in an exact
evidence-backed price report. Missing photos and descriptions are allowed here
because the business priority is to list correctly priced Sparex parts while
catalog intelligence continues to improve.
"""

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

from odoo_apply_website_taxonomy_and_publish import (
    ensure_public_category_path,
    select_website,
)
from odoo_website_taxonomy_agent import clean_text, is_service_or_hidden, map_category, rel_name


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports/product_master/review_reports"
DEFAULT_INPUT = (
    ROOT
    / "odoo_imports/product_master/sparex/pricing/"
    / "odoo_sparex_website_price_apply_report_20260726_233921.csv"
)


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
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


def money(value: Any) -> float:
    text = clean_text(value).replace("$", "").replace(",", "")
    try:
        return round(float(text), 2)
    except ValueError:
        return 0.0


def load_verified_skus(path: Path) -> dict[str, dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    verified: dict[str, dict[str, str]] = {}
    for row in rows:
        sku = clean_text(row.get("Internal Reference"))
        price = money(row.get("New Sales Price"))
        evidence_url = clean_text(row.get("Evidence URLs"))
        if not sku.upper().startswith("S."):
            continue
        if price <= 0:
            continue
        if not evidence_url.startswith(("http://", "https://")):
            continue
        verified[sku] = row
    return verified


def product_row(product: dict[str, Any], product_type_field: str) -> dict[str, str]:
    return {
        "Internal Reference": clean_text(product.get("default_code")),
        "Product Name": clean_text(product.get("name")),
        "Product Category": rel_name(product.get("categ_id")),
        "Product Type": clean_text(product.get(product_type_field)),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish exact-price verified Sparex products.")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--website-name", default="Southern")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    verified_by_sku = load_verified_skus(args.input)
    sku_list = sorted(verified_by_sku)
    if args.limit:
        sku_list = sku_list[: args.limit]

    db, uid, api_key, models = connect()
    website = select_website(models, db, uid, api_key, args.website_name)
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type", "readonly"]})
    product_type_field = "detailed_type" if "detailed_type" in fields_get else "type"
    publish_values: dict[str, Any] = {}
    for field in ("is_published", "website_published"):
        if field in fields_get and not fields_get[field].get("readonly"):
            publish_values[field] = True
    if not publish_values:
        raise SystemExit("No writable publish field found.")

    wanted = [
        "id",
        "default_code",
        "name",
        "active",
        "sale_ok",
        "list_price",
        "categ_id",
        "public_categ_ids",
        "is_published",
        "website_published",
        product_type_field,
    ]
    wanted = [field for field in wanted if field in fields_get]

    product_ids: list[int] = []
    for sku_chunk in chunks(sku_list, 400):
        ids = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "search",
            [[("default_code", "in", sku_chunk)]],
            {"context": {"active_test": False}},
        )
        product_ids.extend(ids)

    rows: list[dict[str, Any]] = []
    publish_ids: list[int] = []
    category_write_groups: dict[int, list[int]] = {}
    category_cache: dict[str, int | None] = {}
    found_skus: set[str] = set()

    for id_chunk in chunks(product_ids, 250):
        products = execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": wanted, "context": {"active_test": False}})
        for product in products:
            sku = clean_text(product.get("default_code"))
            found_skus.add(sku)
            evidence = verified_by_sku.get(sku, {})
            row = product_row(product, product_type_field)
            hidden, hidden_reason = is_service_or_hidden(row)
            website_category, action, notes = map_category(row)
            current_public_ids = product.get("public_categ_ids") or []
            already_published = bool(product.get("is_published")) or bool(product.get("website_published"))
            price = money(product.get("list_price"))
            status = "Ready"
            category_id = None

            if hidden:
                status = "Skipped Hidden"
                notes = hidden_reason
            elif not product.get("active"):
                status = "Skipped Inactive"
                notes = "Product is inactive."
            elif not product.get("sale_ok"):
                status = "Skipped Not Saleable"
                notes = "Product is not saleable."
            elif price <= 0:
                status = "Skipped Missing Price"
                notes = "Verified report had a price, but Odoo currently has no positive sales price."
            elif action == "Review" or not website_category:
                status = "Skipped No Website Category"
            else:
                category_id = ensure_public_category_path(models, db, uid, api_key, website_category, website["id"], args.apply, category_cache)
                if category_id and category_id not in current_public_ids:
                    category_write_groups.setdefault(category_id, []).append(product["id"])
                if already_published:
                    status = "Already Published"
                else:
                    publish_ids.append(product["id"])
                    status = "Ready To Publish"

            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": sku,
                    "Product Name": clean_text(product.get("name")),
                    "Sales Price": price,
                    "Evidence Price": money(evidence.get("New Sales Price")),
                    "Evidence URLs": clean_text(evidence.get("Evidence URLs")),
                    "Website Category": website_category,
                    "Current Public Category Count": len(current_public_ids),
                    "Status": status,
                    "Notes": notes,
                }
            )

    if args.apply:
        for category_id, ids in category_write_groups.items():
            for id_chunk in chunks(ids, 400):
                execute(models, db, uid, api_key, "product.template", "write", [id_chunk, {"public_categ_ids": [(4, category_id)]}])
        for id_chunk in chunks(publish_ids, 400):
            execute(models, db, uid, api_key, "product.template", "write", [id_chunk, publish_values])

    missing_skus = sorted(set(sku_list) - found_skus)
    for sku in missing_skus:
        evidence = verified_by_sku[sku]
        rows.append(
            {
                "Product ID": "",
                "Internal Reference": sku,
                "Product Name": clean_text(evidence.get("Name")),
                "Sales Price": "",
                "Evidence Price": money(evidence.get("New Sales Price")),
                "Evidence URLs": clean_text(evidence.get("Evidence URLs")),
                "Website Category": "",
                "Current Public Category Count": "",
                "Status": "Skipped Missing In Odoo",
                "Notes": "Verified report SKU was not found in Odoo.",
            }
        )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"odoo_publish_verified_sparex_priced_products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Product Name",
                "Sales Price",
                "Evidence Price",
                "Evidence URLs",
                "Website Category",
                "Current Public Category Count",
                "Status",
                "Notes",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "verified_skus": len(verified_by_sku),
            "products_found": len(found_skus),
            "already_published": sum(1 for row in rows if row["Status"] == "Already Published"),
            "ready_to_publish": len(publish_ids),
            "published_now": len(publish_ids) if args.apply else 0,
            "category_writes": sum(len(ids) for ids in category_write_groups.values()) if args.apply else 0,
            "missing_in_odoo": len(missing_skus),
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
