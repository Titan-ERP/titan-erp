from __future__ import annotations

import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT = ROOT / "odoo_imports/product_master/review_reports/web_product_cleanup_opportunities.csv"
OUT = ROOT / "odoo_imports/product_master/review_reports/web_product_cleanup_live_probe.csv"


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

    with REPORT.open(newline="", encoding="utf-8-sig") as f:
        opportunities = list(csv.DictReader(f))

    product_ids = [int(row["Product ID"]) for row in opportunities if row.get("Product ID")]
    category_paths = sorted({row["Proposed Category"] for row in opportunities if row.get("Proposed Category")})

    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [product_ids],
        {"fields": ["id", "default_code", "name", "categ_id", "active"]},
    )
    products_by_id = {row["id"]: row for row in products}

    ext_rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.model.data",
        "search_read",
        [[("model", "=", "product.template"), ("res_id", "in", product_ids)]],
        {"fields": ["module", "name", "res_id"], "limit": len(product_ids) + 20},
    )
    external_by_res_id = {}
    for row in ext_rows:
        external_by_res_id.setdefault(row["res_id"], f"{row['module']}.{row['name']}")

    categories = execute(
        models,
        db,
        uid,
        api_key,
        "product.category",
        "search_read",
        [[("complete_name", "in", category_paths)]],
        {"fields": ["id", "complete_name"], "limit": len(category_paths) + 20},
    )
    category_by_name = {row["complete_name"]: row["id"] for row in categories}

    out_rows = []
    for row in opportunities:
        product_id = int(row["Product ID"])
        product = products_by_id.get(product_id, {})
        category_path = row.get("Proposed Category", "")
        out_rows.append(
            {
                **row,
                "Live Name": product.get("name", ""),
                "Live Internal Reference": product.get("default_code", ""),
                "Live Category": rel_name(product.get("categ_id")),
                "Live Active": product.get("active", ""),
                "Product External ID": external_by_res_id.get(product_id, ""),
                "Proposed Category Exists": "Yes" if category_path in category_by_name else ("No" if category_path else ""),
                "Proposed Category ID": category_by_name.get(category_path, ""),
                "Ready For Direct Update": "Yes"
                if row["Confidence"] == "High" and category_path in category_by_name and product.get("active")
                else "No",
            }
        )

    fields = list(out_rows[0].keys()) if out_rows else []
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    ready = sum(1 for row in out_rows if row["Ready For Direct Update"] == "Yes")
    print(f"Wrote live probe to {OUT}")
    print(f"Ready for direct update: {ready}/{len(out_rows)}")
    missing_categories = sorted({row["Proposed Category"] for row in out_rows if row["Proposed Category"] and row["Proposed Category Exists"] == "No"})
    if missing_categories:
        print("Missing proposed categories:")
        for category in missing_categories:
            print(f"- {category}")


if __name__ == "__main__":
    main()
