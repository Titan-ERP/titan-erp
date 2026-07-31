from __future__ import annotations

import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
AUDIT = ROOT / "odoo_imports/product_master/review_reports/odoo_product_live_inefficiency_audit.csv"
OUT = ROOT / "odoo_imports/product_master/review_reports/odoo_missing_internal_reference_details.csv"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def recommend(row: dict[str, object]) -> str:
    name = str(row.get("name") or "").strip()
    category = rel_name(row.get("categ_id"))
    if category.startswith("Equipment"):
        return "Assign equipment/unit code if this is a rental or fleet asset."
    if category.startswith("Parts"):
        return "Assign vendor/OEM code before purchase/import use."
    if not category:
        if any(term in name.lower() for term in ["fee", "deposit", "service", "labor"]):
            return "Categorize as fee/service and assign service code if sold."
        return "Review category first, then assign internal reference."
    return "Assign internal reference or archive if obsolete."


def main() -> None:
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    with AUDIT.open(newline="", encoding="utf-8-sig") as f:
        audit_rows = [row for row in csv.DictReader(f) if row["Issue"] == "Missing Internal Reference"]
    product_ids = [int(row["Product ID"]) for row in audit_rows]
    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [product_ids],
        {
            "fields": [
                "id",
                "name",
                "active",
                "categ_id",
                "type",
                "is_storable",
                "sale_ok",
                "purchase_ok",
                "standard_price",
                "list_price",
                "seller_ids",
                "x_studio_manufacturer",
            ],
            "context": {"active_test": False},
        },
    )
    rows = []
    for product in products:
        rows.append(
            {
                "Product ID": product["id"],
                "Name": product.get("name") or "",
                "Category": rel_name(product.get("categ_id")),
                "Product Type": product.get("type") or "",
                "Storable": product.get("is_storable"),
                "Sale OK": product.get("sale_ok"),
                "Purchase OK": product.get("purchase_ok"),
                "Cost": product.get("standard_price"),
                "Sales Price": product.get("list_price"),
                "Vendor Lines": len(product.get("seller_ids") or []),
                "Manufacturer": product.get("x_studio_manufacturer") or "",
                "Recommended Next Step": recommend(product),
            }
        )
    rows.sort(key=lambda row: (row["Category"], row["Name"]))
    fields = [
        "Product ID",
        "Name",
        "Category",
        "Product Type",
        "Storable",
        "Sale OK",
        "Purchase OK",
        "Cost",
        "Sales Price",
        "Vendor Lines",
        "Manufacturer",
        "Recommended Next Step",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
