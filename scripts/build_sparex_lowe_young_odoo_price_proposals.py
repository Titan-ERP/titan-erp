"""Build Odoo price proposals from Lowe & Young Sparex price evidence.

Only products whose current Odoo sales price is <= $1 are marked
Ready For Review. Existing non-placeholder prices are kept as evidence only.
"""

from __future__ import annotations

import argparse
import csv
import os
import socket
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports/product_master/pricing"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    socket.setdefaulttimeout(90)
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def parse_price(value: Any) -> float:
    try:
        return float(str(value or "0").replace(",", "").strip())
    except ValueError:
        return 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create Odoo price proposals from Lowe & Young Sparex evidence.")
    parser.add_argument("evidence_csv", type=Path)
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()

    evidence_rows = list(csv.DictReader(args.evidence_csv.open("r", encoding="utf-8-sig", newline="")))
    by_sku: dict[str, dict[str, str]] = {}
    for row in evidence_rows:
        sku = (row.get("Internal Reference") or "").strip()
        price = parse_price(row.get("Evidence Price"))
        if not sku or price <= 1:
            continue
        by_sku.setdefault(sku, row)

    db, uid, api_key, models = connect()
    products: dict[str, dict[str, Any]] = {}
    for chunk in chunks(sorted(by_sku), 250):
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "search_read",
            [[("default_code", "in", chunk)]],
            {
                "fields": ["id", "default_code", "name", "list_price", "standard_price", "website_published", "is_published"],
                "limit": 300,
                "context": {"active_test": False},
            },
        )
        for row in rows:
            products[row["default_code"]] = row

    proposal_rows: list[dict[str, Any]] = []
    for sku, evidence in sorted(by_sku.items()):
        product = products.get(sku)
        observed_price = parse_price(evidence.get("Evidence Price"))
        if not product:
            status = "No Odoo Match"
        elif float(product.get("list_price") or 0.0) <= 1.0:
            status = "Ready For Review"
        else:
            status = "Evidence Only - Existing Real Price"
        proposal_rows.append(
            {
                "ID": product["id"] if product else "",
                "Internal Reference": sku,
                "Name": product["name"] if product else evidence.get("Evidence Name", ""),
                "Current Sales Price": product.get("list_price", "") if product else "",
                "Cost": product.get("standard_price", "") if product else "",
                "Proposed Sales Price": f"{observed_price:.2f}",
                "USD Observations": f"{observed_price:.2f}",
                "GBP Observations": "",
                "Sources": evidence.get("Evidence Source") or "Lowe & Young",
                "Source URLs": evidence.get("Evidence URL", ""),
                "Status": status,
                "Notes": "Exact Sparex SKU public US dealer listing. Ready rows only update placeholder Odoo prices.",
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_name = args.run_name or args.evidence_csv.stem
    out_path = OUT_DIR / f"{run_name}_odoo_price_proposals.csv"
    fields = [
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
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(proposal_rows)

    print(f"Wrote {out_path}")
    print(f"Evidence SKUs: {len(by_sku)}")
    print(f"Odoo matches: {sum(1 for row in proposal_rows if row['ID'])}")
    print(f"Ready For Review: {sum(1 for row in proposal_rows if row['Status'] == 'Ready For Review')}")
    print(f"Existing real price evidence only: {sum(1 for row in proposal_rows if row['Status'] == 'Evidence Only - Existing Real Price')}")
    print(f"No Odoo match: {sum(1 for row in proposal_rows if row['Status'] == 'No Odoo Match')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
