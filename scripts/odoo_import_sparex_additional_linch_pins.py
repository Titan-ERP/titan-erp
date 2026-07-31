from __future__ import annotations

import csv
import os
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex"
IMPORT_CSV = OUT_DIR / "sparex_additional_linch_pins_import.csv"
RESULTS_CSV = OUT_DIR / "sparex_additional_linch_pins_odoo_results.csv"

TARGET_CATEGORY = "Parts / Hardware"
VENDOR_NAME = "Sparex"
BUY_ROUTE_NAME = "Buy"
MTO_ROUTE_NAME = "Replenish on Order (MTO)"

PARTS = [
    {"code": "S.8460", "name": "Pear Linch Pin - Pin 11 x 70 mm", "detail": "", "url": "https://us.sparex.com/fasteners-hardware/linch-pins/pear.html"},
    {"code": "S.33013", "name": "Pear Linch Pin - Pin 12 x 52 mm", "detail": "Fits lower link quick release cone", "url": "https://us.sparex.com/fasteners-hardware/linch-pins/pear.html"},
    {"code": "S.33014", "name": "Pear Linch Pin - Pin 15 x 60 mm", "detail": "Fits lower link quick release cone", "url": "https://us.sparex.com/fasteners-hardware/linch-pins/pear.html"},
    {"code": "S.16496", "name": "Stabilizer Linch Pin", "detail": "Linch pin to fit old 2 piece welded tube", "url": "https://us.sparex.com/fasteners-hardware/linch-pins/stabilizer-linch-pins.html"},
    {"code": "S.8605", "name": "Stabilizer Pin - Pin 16 x 62 mm", "detail": "", "url": "https://us.sparex.com/fasteners-hardware/linch-pins/stabilizer-linch-pins.html"},
    {"code": "S.11637", "name": "Stabilizer Pin - Pin 19 x 90 mm", "detail": "", "url": "https://us.sparex.com/fasteners-hardware/linch-pins/stabilizer-linch-pins.html"},
]


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


def category_id(models, db, uid, api_key, complete_name: str) -> int:
    rows = execute(models, db, uid, api_key, "product.category", "search_read", [[("complete_name", "=", complete_name)]], {"fields": ["id"], "limit": 1})
    if not rows:
        raise SystemExit(f"Missing category: {complete_name}")
    return rows[0]["id"]


def write_import_csv() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = ["Internal Reference", "Name", "Product Category", "Manufacturer", "Vendors", "Vendors Product Code", "Vendors/price", "Vendors/delay", "Vendors/min_qty", "Purchase Description", "Source URL"]
    with IMPORT_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for part in PARTS:
            writer.writerow(
                {
                    "Internal Reference": part["code"],
                    "Name": part["name"],
                    "Product Category": TARGET_CATEGORY,
                    "Manufacturer": "Sparex",
                    "Vendors": VENDOR_NAME,
                    "Vendors Product Code": part["code"],
                    "Vendors/price": "0.00",
                    "Vendors/delay": "1",
                    "Vendors/min_qty": "1",
                    "Purchase Description": f"{part['name']}. {part['detail']}. Source: {part['url']}".strip(),
                    "Source URL": part["url"],
                }
            )


def main() -> None:
    write_import_csv()
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    cat_id = category_id(models, db, uid, api_key, TARGET_CATEGORY)
    vendor_id = ensure_partner(models, db, uid, api_key, VENDOR_NAME)
    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    procurement_values = sparex_procurement_values(models, db, uid, api_key, product_fields)

    existing = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_read",
        [[("default_code", "in", [part["code"] for part in PARTS])]],
        {"fields": ["id", "default_code"], "limit": 100},
    )
    existing_by_code = {row["default_code"]: row["id"] for row in existing}

    results = []
    for part in PARTS:
        values = {
            "default_code": part["code"],
            "name": part["name"],
            "categ_id": cat_id,
            "sale_ok": True,
            "purchase_ok": True,
            "x_studio_manufacturer": "Sparex",
            "description_purchase": f"{part['name']}. {part['detail']}. Source: {part['url']}".strip(),
        }
        if "is_storable" in product_fields:
            values["is_storable"] = True
        elif "type" in product_fields:
            values["type"] = "product"
        values.update(procurement_values)

        status = "Updated"
        product_id = existing_by_code.get(part["code"])
        if product_id:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], values])
        else:
            product_id = execute(models, db, uid, api_key, "product.template", "create", [values])
            status = "Created"

        suppliers = execute(models, db, uid, api_key, "product.supplierinfo", "search_read", [[("partner_id", "=", vendor_id), ("product_tmpl_id", "=", product_id)]], {"fields": ["id"], "limit": 1})
        supplier_values = {"partner_id": vendor_id, "product_tmpl_id": product_id, "product_code": part["code"], "price": 0.0, "delay": 1, "min_qty": 1}
        if suppliers:
            execute(models, db, uid, api_key, "product.supplierinfo", "write", [[suppliers[0]["id"]], supplier_values])
        else:
            execute(models, db, uid, api_key, "product.supplierinfo", "create", [supplier_values])

        after = execute(models, db, uid, api_key, "product.template", "read", [[product_id]], {"fields": ["default_code", "name", "categ_id", "x_studio_manufacturer", "seller_ids"], "context": {"active_test": False}})[0]
        verified = after.get("default_code") == part["code"] and after.get("name") == part["name"] and rel_name(after.get("categ_id")) == TARGET_CATEGORY and (after.get("x_studio_manufacturer") or "") == "Sparex" and bool(after.get("seller_ids"))
        results.append({"Timestamp": datetime.now().isoformat(timespec="seconds"), "Status": status, "Verified": "Yes" if verified else "No", "Product ID": product_id, "Internal Reference": part["code"], "Name": part["name"], "Category": TARGET_CATEGORY, "Vendor": VENDOR_NAME, "Source URL": part["url"]})

    fields = ["Timestamp", "Status", "Verified", "Product ID", "Internal Reference", "Name", "Category", "Vendor", "Source URL"]
    with RESULTS_CSV.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    failed = [row for row in results if row["Verified"] != "Yes"]
    print(f"Import CSV: {IMPORT_CSV}")
    print(f"Results CSV: {RESULTS_CSV}")
    print(f"Created: {sum(1 for row in results if row['Status'] == 'Created')}")
    print(f"Updated: {sum(1 for row in results if row['Status'] == 'Updated')}")
    print(f"Verified: {len(results) - len(failed)}/{len(results)}")
    if failed:
        raise SystemExit("Some additional Sparex imports failed verification.")


if __name__ == "__main__":
    main()
