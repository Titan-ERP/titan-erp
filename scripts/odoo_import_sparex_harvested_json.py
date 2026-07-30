from __future__ import annotations

import csv
import html
import json
import os
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
DEFAULT_JSON_PATH = ROOT / "odoo_imports/product_master/sparex/sparex_harvested_products.json"
BUY_ROUTE_NAME = "Buy"
MTO_ROUTE_NAME = "Replenish on Order (MTO)"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def sparex_procurement_values(models, db, uid, api_key, product_fields: dict) -> dict:
    values = {}
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


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def ensure_partner(models, db, uid, api_key, name: str) -> int:
    rows = execute(models, db, uid, api_key, "res.partner", "search_read", [[("name", "=", name)]], {"fields": ["id"], "limit": 1})
    if rows:
        return rows[0]["id"]
    return execute(models, db, uid, api_key, "res.partner", "create", [{"name": name, "supplier_rank": 1, "company_type": "company"}])


def get_category_id(models, db, uid, api_key, complete_name: str) -> int:
    rows = execute(models, db, uid, api_key, "product.category", "search_read", [[("complete_name", "=", complete_name)]], {"fields": ["id"], "limit": 1})
    if not rows:
        raise SystemExit(f"Missing category: {complete_name}")
    return rows[0]["id"]


def flatten_oem_numbers(record: dict) -> list[str]:
    values = []
    for group in record.get("oem_part_numbers", []):
        values.extend(group.get("part_numbers", []))
    return values


def plain_summary(record: dict) -> str:
    product = record["product"]
    specs = record.get("specifications", {})
    oem_lines = []
    for group in record.get("oem_part_numbers", []):
        oem_lines.append(f"{group['make']}: {', '.join(group['part_numbers'])}")
    fitment_lines = []
    for group in record.get("fitment", []):
        models = ", ".join(group.get("models", []))
        fitment_lines.append(f"{group['make']}: {models}")
    catalog_lines = []
    for group in record.get("catalog_pages", []):
        catalog_lines.append(f"{group['catalog']}: {', '.join(group['pages'])}")
    return "\n".join(
        [
            f"Sparex source: {record['source']['url']}",
            f"Product: {product['name']}",
            f"Specs: {specs.get('type', '')}; {specs.get('voltage', '')}; {specs.get('notes', '')}",
            "",
            "OEM Cross References:",
            *oem_lines,
            "",
            "Suitable For Make/Model:",
            *fitment_lines,
            "",
            "Catalog Pages:",
            *catalog_lines,
        ]
    ).strip()


def html_summary(record: dict) -> str:
    text = plain_summary(record)
    return "<pre>" + html.escape(text) + "</pre>"


def main() -> None:
    load_env()
    json_path = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else DEFAULT_JSON_PATH
    results_csv = json_path.with_name(f"{json_path.stem}_odoo_results.csv")
    records = json.loads(json_path.read_text(encoding="utf-8"))
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    procurement_values = sparex_procurement_values(models, db, uid, api_key, product_fields)
    results = []
    for record in records:
        product = record["product"]
        category_id = get_category_id(models, db, uid, api_key, product["category"])
        vendor_id = ensure_partner(models, db, uid, api_key, record["source"]["vendor"])
        existing = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "search_read",
            [[("default_code", "=", product["internal_reference"])]],
            {"fields": ["id", "default_code"], "limit": 1},
        )

        values = {
            "default_code": product["internal_reference"],
            "name": product["name"],
            "categ_id": category_id,
            "sale_ok": True,
            "purchase_ok": True,
            "x_studio_manufacturer": product["manufacturer"],
            "x_studio_oem_part_number": ", ".join(flatten_oem_numbers(record)),
            "description_purchase": plain_summary(record),
            "description_sale": plain_summary(record),
            "description": html_summary(record),
            "website_description": html_summary(record),
        }
        if "is_storable" in product_fields:
            values["is_storable"] = True
        elif "type" in product_fields:
            values["type"] = "product"
        values.update(procurement_values)

        status = "Updated"
        if existing:
            product_id = existing[0]["id"]
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], values])
        else:
            product_id = execute(models, db, uid, api_key, "product.template", "create", [values])
            status = "Created"

        supplier_rows = execute(
            models,
            db,
            uid,
            api_key,
            "product.supplierinfo",
            "search_read",
            [[("partner_id", "=", vendor_id), ("product_tmpl_id", "=", product_id)]],
            {"fields": ["id"], "limit": 1},
        )
        supplier_values = {
            "partner_id": vendor_id,
            "product_tmpl_id": product_id,
            "product_code": product["vendor_code"],
            "price": product["vendor_price"],
            "delay": product["lead_time_days"],
            "min_qty": 1,
        }
        if supplier_rows:
            execute(models, db, uid, api_key, "product.supplierinfo", "write", [[supplier_rows[0]["id"]], supplier_values])
        else:
            execute(models, db, uid, api_key, "product.supplierinfo", "create", [supplier_values])

        after = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [[product_id]],
            {"fields": ["default_code", "name", "categ_id", "x_studio_oem_part_number", "seller_ids"], "context": {"active_test": False}},
        )[0]
        verified = (
            after.get("default_code") == product["internal_reference"]
            and after.get("name") == product["name"]
            and rel_name(after.get("categ_id")) == product["category"]
            and bool(after.get("seller_ids"))
        )
        results.append(
            {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Status": status,
                "Verified": "Yes" if verified else "No",
                "Product ID": product_id,
                "Internal Reference": product["internal_reference"],
                "Name": product["name"],
                "OEM Count": len(flatten_oem_numbers(record)),
                "Fitment Makes": len(record.get("fitment", [])),
                "Catalog References": len(record.get("catalog_pages", [])),
                "Source URL": record["source"]["url"],
            }
        )

    fields = ["Timestamp", "Status", "Verified", "Product ID", "Internal Reference", "Name", "OEM Count", "Fitment Makes", "Catalog References", "Source URL"]
    with results_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    failed = [row for row in results if row["Verified"] != "Yes"]
    print(f"JSON source: {json_path}")
    print(f"Results: {results_csv}")
    print(f"Rows: {len(results)}")
    print(f"Verified: {len(results) - len(failed)}/{len(results)}")
    if failed:
        raise SystemExit("Some harvested JSON imports failed verification.")


if __name__ == "__main__":
    main()
