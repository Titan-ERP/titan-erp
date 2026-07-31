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
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def gross_margin(price: float, cost: float) -> float:
    if price <= 0:
        return 0.0
    return (price - cost) / price


def southern_company_id(models, db, uid, api_key) -> int:
    env_company_id = os.environ.get("ODOO_COMPANY_ID") or os.environ.get("SOUTHERN_ODOO_COMPANY_ID")
    if env_company_id and env_company_id.isdigit():
        return int(env_company_id)
    company_ids = execute(
        models,
        db,
        uid,
        api_key,
        "res.company",
        "search",
        [[("name", "ilike", "Southern Equipment Company")]],
        {"limit": 1},
    )
    if company_ids:
        return int(company_ids[0])
    return 2


def read_partner_prices(models, db, uid, api_key, product_ids: list[int], company_id: int) -> dict[int, float]:
    if not product_ids:
        return {}
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [product_ids],
        {
            "fields": ["id", "southern_partner_price"],
            "context": {"allowed_company_ids": [company_id], "active_test": False},
        },
    )
    return {int(row["id"]): float(row.get("southern_partner_price") or 0.0) for row in rows}


def proposal_for(cost: float, retail: float, floor: float, target: float) -> tuple[float | None, str, str]:
    if cost <= 0:
        return None, "Blocked - Missing Verified Cost", "Cost is missing or zero."
    if retail <= 1.0:
        return None, "Blocked - Retail Missing Or Placeholder", "Public Sales Price is missing or still at placeholder."
    retail_margin = gross_margin(retail, cost)
    if retail_margin < floor:
        return None, "Blocked - Retail Margin Below Floor", "Retail price does not leave the minimum gross margin."
    discount_candidates: tuple[float, ...]
    if retail_margin >= 0.45:
        discount_candidates = (0.15, 0.10, 0.05)
    elif retail_margin >= 0.35:
        discount_candidates = (0.15, 0.10, 0.05)
    elif retail_margin >= 0.25:
        discount_candidates = (0.10, 0.05)
    else:
        discount_candidates = (0.05,)
    for discount in discount_candidates:
        candidate = round(retail * (1 - discount), 2)
        if candidate > cost and gross_margin(candidate, cost) >= target:
            pct = int(discount * 100)
            return candidate, f"Ready For Partner Price Apply - {pct}% Discount", f"{pct}% off retail leaves target margin."
    if 0.05 in discount_candidates:
        candidate = round(retail * 0.95, 2)
        candidate_margin = gross_margin(candidate, cost)
        if candidate > cost and floor <= candidate_margin < target:
            return candidate, "Ready For Partner Price Apply - 5% Discount", "5% off retail leaves floor margin but not target margin."
    return None, "Blocked - No Automatic Discount Room", "Any automatic discount would miss the approved margin gates."


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Southern Partner Price proposals from verified cost and retail.")
    parser.add_argument("--sku", action="append", default=[], help="Limit to one SKU. Can be passed multiple times.")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--run-name", default="")
    parser.add_argument("--min-gross-margin", type=float, default=0.15)
    parser.add_argument("--target-gross-margin", type=float, default=0.20)
    parser.add_argument("--sparex-only", action="store_true", default=True)
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    company_id = southern_company_id(models, db, uid, api_key)
    domain: list[Any] = [("default_code", "=like", "S.%")] if args.sparex_only else []
    if args.sku:
        domain.append(("default_code", "in", args.sku))
    product_rows = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_read",
        [domain],
        {
            "fields": [
                "id",
                "default_code",
                "name",
                "standard_price",
                "list_price",
                "southern_source_name",
                "southern_source_url",
                "website_published",
                "is_published",
                "website_url",
            ],
            "limit": args.limit,
            "order": "write_date desc, id desc",
            "context": {"active_test": False},
        },
    )
    current_partner_prices = read_partner_prices(models, db, uid, api_key, [int(product["id"]) for product in product_rows], company_id)

    proposal_rows = []
    for product in product_rows:
        cost = float(product.get("standard_price") or 0.0)
        retail = float(product.get("list_price") or 0.0)
        current_partner = current_partner_prices.get(int(product["id"]), 0.0)
        proposed, status, notes = proposal_for(cost, retail, args.min_gross_margin, args.target_gross_margin)
        if proposed is not None and round(current_partner, 2) == proposed:
            status = "No Change"
            notes = "Current Partner Price already matches proposal."
        partner_margin = gross_margin(proposed or 0.0, cost) if proposed else None
        retail_margin = gross_margin(retail, cost) if retail else None
        proposal_rows.append(
            {
                "ID": product["id"],
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Cost": f"{cost:.2f}",
                "Current Sales Price": f"{retail:.2f}",
                "Current Partner Price": f"{current_partner:.2f}",
                "Proposed Partner Price": f"{proposed:.2f}" if proposed is not None else "",
                "Retail Gross Margin %": f"{retail_margin * 100:.1f}" if retail_margin is not None else "",
                "Partner Gross Margin %": f"{partner_margin * 100:.1f}" if partner_margin is not None else "",
                "Partner Discount %": f"{(1 - proposed / retail) * 100:.1f}" if proposed and retail > 0 else "",
                "Source Name": product.get("southern_source_name") or "",
                "Source URL": product.get("southern_source_url") or "",
                "Website Published": product.get("website_published"),
                "Is Published": product.get("is_published"),
                "Website URL": product.get("website_url") or "",
                "Status": status,
                "Notes": notes,
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    run_name = args.run_name or f"partner_price_proposals_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    out_path = OUT_DIR / f"{run_name}.csv"
    fieldnames = [
        "ID",
        "Internal Reference",
        "Name",
        "Cost",
        "Current Sales Price",
        "Current Partner Price",
        "Proposed Partner Price",
        "Retail Gross Margin %",
        "Partner Gross Margin %",
        "Partner Discount %",
        "Source Name",
        "Source URL",
        "Website Published",
        "Is Published",
        "Website URL",
        "Status",
        "Notes",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(proposal_rows)

    ready = sum(1 for row in proposal_rows if row["Status"].startswith("Ready For Partner Price Apply"))
    blocked = sum(1 for row in proposal_rows if row["Status"].startswith("Blocked"))
    no_change = sum(1 for row in proposal_rows if row["Status"] == "No Change")
    print(f"Wrote {out_path}")
    print(f"Products observed: {len(proposal_rows)}")
    print(f"Ready rows: {ready}")
    print(f"Blocked rows: {blocked}")
    print(f"No-change rows: {no_change}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
