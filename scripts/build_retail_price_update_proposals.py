from __future__ import annotations

import argparse
import csv
import os
import xmlrpc.client
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Odoo retail price proposals from public web price observations.")
    parser.add_argument("research_csv", type=Path)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--min-usd-observations", type=int, default=1)
    args = parser.parse_args()

    observations = list(csv.DictReader(args.research_csv.open(encoding="utf-8-sig")))
    by_sku: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in observations:
        sku = row["Internal Reference"].strip()
        if sku:
            by_sku[sku].append(row)

    db, uid, api_key, models = connect()
    product_rows = []
    for sku_chunk in chunks(sorted(by_sku), 250):
        product_rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "search_read",
                [[("default_code", "in", sku_chunk)]],
                {
                    "fields": ["id", "default_code", "name", "list_price", "standard_price", "website_published"],
                    "limit": 300,
                    "context": {"active_test": False},
                },
            )
        )
    products = {row["default_code"]: row for row in product_rows}

    proposal_rows = []
    for sku, sku_observations in sorted(by_sku.items()):
        product = products.get(sku)
        usd_prices = [float(row["Observed Retail Price"]) for row in sku_observations if row["Currency"] == "USD"]
        gbp_prices = [float(row["Observed Retail Price"]) for row in sku_observations if row["Currency"] == "GBP"]
        proposed_price = round(median(usd_prices), 2) if usd_prices else ""
        if not product:
            status = "No Odoo Match"
        elif proposed_price == "":
            status = "Evidence Only - Currency Review"
        elif len(usd_prices) < args.min_usd_observations:
            status = "Evidence Only - Need More Retailers"
        elif float(product.get("standard_price") or 0.0) > 0 and proposed_price <= float(product.get("standard_price") or 0.0):
            status = "Blocked - Median Not Above Cost"
        elif float(product.get("list_price") or 0.0) == proposed_price:
            status = "No Change"
        else:
            status = "Ready For Median Retailer Apply"
        proposal_rows.append(
            {
                "ID": product["id"] if product else "",
                "Internal Reference": sku,
                "Name": product["name"] if product else "",
                "Current Sales Price": product.get("list_price", "") if product else "",
                "Cost": product.get("standard_price", "") if product else "",
                "Proposed Sales Price": proposed_price,
                "USD Observations": "; ".join(str(price) for price in usd_prices),
                "GBP Observations": "; ".join(str(price) for price in gbp_prices),
                "Sources": "; ".join(sorted({row["Source"] for row in sku_observations})),
                "Source URLs": "; ".join(row["Source URL"] for row in sku_observations[:5]),
                "Status": status,
                "Notes": (
                    "Median public USD retailer rule. GBP prices remain comparison evidence until a conversion/freight rule is approved. "
                    "Blocked if median is not above current Odoo Cost."
                ),
            }
        )

    run_name = args.run_name or args.research_csv.stem.replace("research", "proposal")
    out_path = OUT_DIR / f"{run_name}_odoo_price_proposals.csv"
    fieldnames = [
        "ID",
        "Internal Reference",
        "Name",
        "Current Sales Price",
        "Cost",
        "Proposed Sales Price",
        "USD Observations",
        "GBP Observations",
        "Sources",
        "Source URLs",
        "Status",
        "Notes",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(proposal_rows)

    print(f"Wrote {out_path}")
    print(f"Observed SKUs: {len(by_sku)}")
    print(f"Odoo matches: {sum(1 for row in proposal_rows if row['ID'])}")
    print(f"Ready for median retailer apply: {sum(1 for row in proposal_rows if row['Status'] == 'Ready For Median Retailer Apply')}")
    print(f"Evidence only: {sum(1 for row in proposal_rows if row['Status'] == 'Evidence Only - Currency Review')}")
    print(f"No Odoo match: {sum(1 for row in proposal_rows if row['Status'] == 'No Odoo Match')}")


if __name__ == "__main__":
    main()

