"""Propose or apply cost-based retail pricing for Southern parts.

This uses the documented Sparex/Southern pricing matrix when Odoo has a
non-zero standard cost. Default mode is a dry run. Publishing is a separate,
explicit action and requires an existing website category.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
from datetime import datetime
from pathlib import Path
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "outputs"
MEMBERSHIP_CODE = "SEC-MEMBERSHIP-STANDARD"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip("\"").strip("'")


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def margin_for(category: str, cost: float) -> float:
    category = (category or "").lower()
    overrides = [
        (r"hardware|fastener|pin|bolt|washer|nut", 0.58),
        (r"filter", 0.45),
        (r"hydraulic", 0.45),
        (r"electrical|electrics|switch|sensor", 0.42),
        (r"cab|sheet metal|engine|pto|driveline|power transmission|axle", 0.38),
        (r"workshop|tool|merchandising", 0.35),
    ]
    for pattern, margin in overrides:
        if re.search(pattern, category):
            return margin
    if cost < 5:
        return 0.60
    if cost < 25:
        return 0.55
    if cost < 100:
        return 0.45
    if cost < 500:
        return 0.38
    return 0.30


def round_retail(raw: float) -> float:
    if raw < 10:
        # Next half-dollar ending in .49 or .99.
        return math.ceil((raw - 0.49) * 2) / 2 + 0.49
    if raw < 100:
        return math.ceil(raw) - 0.01
    if raw < 500:
        return math.ceil(raw / 5) * 5 - 0.01
    return math.ceil(raw / 10) * 10 - 0.01


def retail_price(cost: float, category: str) -> float:
    margin = margin_for(category, cost)
    raw = cost / (1 - margin)
    return max(round(round_retail(raw), 2), 1.49)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write proposed sales prices")
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Publish priced products that already have a website category; requires --apply",
    )
    args = parser.parse_args()
    if args.publish and not args.apply:
        parser.error("--publish requires --apply")

    db, uid, api_key, models = connect()
    fields = [
        "id",
        "name",
        "default_code",
        "list_price",
        "standard_price",
        "is_published",
        "sale_ok",
        "active",
        "categ_id",
        "public_categ_ids",
    ]
    domain = [
        ("default_code", "!=", MEMBERSHIP_CODE),
        ("sale_ok", "=", True),
        ("active", "=", True),
        ("is_published", "=", False),
        ("standard_price", ">", 0),
        ("list_price", "<=", 1.0),
    ]

    products = []
    offset = 0
    while True:
        batch = models.execute_kw(
            db,
            uid,
            api_key,
            "product.template",
            "search_read",
            [domain],
            {"fields": fields, "limit": 1000, "offset": offset},
        )
        if not batch:
            break
        products.extend(batch)
        offset += len(batch)

    rows = []
    priced_ids = []
    published_ids = []
    for product in products:
        code = (product.get("default_code") or "").strip()
        category = product.get("categ_id") and product["categ_id"][1] or ""
        cost = float(product.get("standard_price") or 0)
        old_price = float(product.get("list_price") or 0)
        new_price = retail_price(cost, category)
        publish_eligible = bool(
            code and new_price > 1 and product.get("public_categ_ids")
        )
        status = "Would Price"
        if args.apply:
            values = {"list_price": new_price}
            status = "Priced"
            if args.publish and publish_eligible:
                values["is_published"] = True
                status = "Priced and Published"
                published_ids.append(product["id"])
            models.execute_kw(
                db,
                uid,
                api_key,
                "product.template",
                "write",
                [[product["id"]], values],
            )
            priced_ids.append(product["id"])
        rows.append(
            {
                "mode": "apply" if args.apply else "dry_run",
                "status": status,
                "id": product["id"],
                "default_code": code,
                "name": product.get("name") or "",
                "category": category,
                "standard_price": cost,
                "old_list_price": old_price,
                "new_list_price": new_price,
                "has_website_category": bool(product.get("public_categ_ids")),
                "publish_eligible": publish_eligible,
                "method": "cost_matrix",
                "margin": margin_for(category, cost),
            }
        )
    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report = OUT_DIR / f"southern_cost_based_retail_pricing_apply_{stamp}.csv"
    with report.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "checked_costed_unpublished_placeholders": len(products),
            "planned_prices": len(rows),
            "priced": len(priced_ids),
            "published": len(published_ids),
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
