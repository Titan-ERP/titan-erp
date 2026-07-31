from __future__ import annotations

import argparse
import csv
import os
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "blumaq" / "pricing"


def load_env() -> None:
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a Blumaq retail-price research queue from live Odoo BLQ products.")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()

    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    ids = execute(models, db, uid, api_key, "product.template", "search", [[("default_code", "=like", "BLQ-%")]], {"limit": args.limit, "context": {"active_test": False}})
    rows = []
    for id_chunk in chunks(ids, 250):
        rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [id_chunk],
                {
                    "fields": [
                        "id",
                        "default_code",
                        "name",
                        "list_price",
                        "standard_price",
                        "categ_id",
                        "description_purchase",
                        "image_1920",
                    ],
                    "context": {"active_test": False, "bin_size": True},
                },
            )
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or f"blumaq_retail_price_research_queue_{stamp}"
    out_path = OUT_DIR / f"{run_name}.csv"
    fieldnames = [
        "ID",
        "Internal Reference",
        "Supplier SKU",
        "Name",
        "Current Sales Price",
        "Cost",
        "Category",
        "Has Image",
        "Search Query 1",
        "Search Query 2",
        "Search Query 3",
        "Status",
        "Notes",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            internal_ref = row.get("default_code") or ""
            supplier_sku = internal_ref.replace("BLQ-", "", 1)
            name = row.get("name") or ""
            category = row.get("categ_id")[1] if row.get("categ_id") else ""
            current_price = float(row.get("list_price") or 0.0)
            status = "Needs Research"
            if current_price and current_price != 1.0:
                status = "Has Non-Placeholder Price - Review"
            writer.writerow(
                {
                    "ID": row["id"],
                    "Internal Reference": internal_ref,
                    "Supplier SKU": supplier_sku,
                    "Name": name,
                    "Current Sales Price": current_price,
                    "Cost": float(row.get("standard_price") or 0.0),
                    "Category": category,
                    "Has Image": "Yes" if row.get("image_1920") else "No",
                    "Search Query 1": f'"{supplier_sku}" price',
                    "Search Query 2": f'"{supplier_sku}" "{name.split(" - Blumaq ")[0]}"',
                    "Search Query 3": f'"{supplier_sku}" Caterpillar price',
                    "Status": status,
                    "Notes": "Use exact SKU/OEM retail evidence only. Do not use Blumaq public catalog pages as price evidence because they do not show public retail pricing.",
                }
            )

    print(f"Wrote {out_path}")
    print(f"Rows: {len(rows)}")
    print(f"Missing images: {sum(1 for row in rows if not row.get('image_1920'))}")
    print(f"Placeholder $1 prices: {sum(1 for row in rows if float(row.get('list_price') or 0.0) == 1.0)}")


if __name__ == "__main__":
    main()
