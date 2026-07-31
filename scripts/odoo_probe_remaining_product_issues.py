from __future__ import annotations

import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
AUDIT = ROOT / "odoo_imports/product_master/review_reports/odoo_product_live_inefficiency_audit.csv"
OUT = ROOT / "odoo_imports/product_master/review_reports/odoo_remaining_product_issue_details.csv"


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
        issue_rows = [row for row in csv.DictReader(f) if row["Issue"] in {"No Vendor Line", "Duplicate Internal Reference", "Archive Candidate Still Active"}]

    product_ids = sorted({int(row["Product ID"]) for row in issue_rows if row.get("Product ID")})
    requested_fields = [
        "id",
        "default_code",
        "name",
        "active",
        "categ_id",
        "is_storable",
        "sale_ok",
        "purchase_ok",
        "standard_price",
        "list_price",
        "seller_ids",
        "x_studio_manufacturer",
    ]
    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    for optional in ["type", "detailed_type"]:
        if optional in product_fields:
            requested_fields.append(optional)

    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [product_ids],
        {
            "fields": requested_fields,
            "context": {"active_test": False},
        },
    )
    by_id = {row["id"]: row for row in products}
    issues_by_id: dict[int, list[str]] = {}
    for row in issue_rows:
        issues_by_id.setdefault(int(row["Product ID"]), []).append(row["Issue"])

    out_rows = []
    for product_id in product_ids:
        product = by_id[product_id]
        out_rows.append(
            {
                "Issues": "; ".join(sorted(set(issues_by_id[product_id]))),
                "Product ID": product_id,
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Active": product.get("active"),
                "Category": rel_name(product.get("categ_id")),
                "Product Type": product.get("detailed_type") or product.get("type") or "",
                "Storable": product.get("is_storable"),
                "Sale OK": product.get("sale_ok"),
                "Purchase OK": product.get("purchase_ok"),
                "Cost": product.get("standard_price"),
                "Sales Price": product.get("list_price"),
                "Vendor Lines": len(product.get("seller_ids") or []),
                "Manufacturer": product.get("x_studio_manufacturer") or "",
            }
        )

    fields = [
        "Issues",
        "Product ID",
        "Internal Reference",
        "Name",
        "Active",
        "Category",
        "Product Type",
        "Storable",
        "Sale OK",
        "Purchase OK",
        "Cost",
        "Sales Price",
        "Vendor Lines",
        "Manufacturer",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)
    print(f"Wrote {len(out_rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
