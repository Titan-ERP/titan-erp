"""Dry-run or publish Southern parts that pass ecommerce pricing guardrails.

Default mode writes a CSV report only. Use --apply after retail pricing has
been reviewed and updated in Odoo.
"""

from __future__ import annotations

import argparse
import csv
import os
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


def reason_for_block(product: dict) -> str:
    code = (product.get("default_code") or "").strip()
    price = float(product.get("list_price") or 0)
    cost = float(product.get("standard_price") or 0)
    name = (product.get("name") or "").strip()
    reasons = []
    if code == MEMBERSHIP_CODE:
        reasons.append("membership_product")
    if price <= 1:
        reasons.append("placeholder_or_missing_price")
    if not code:
        reasons.append("missing_internal_reference")
    if not name:
        reasons.append("missing_name")
    if not product.get("public_categ_ids"):
        reasons.append("missing_website_category")
    if cost <= 0:
        reasons.append("missing_cost_warning")
    if any(
        marker.lower() in str(product.get(field) or "").lower()
        for field in (
            "description_ecommerce",
            "website_description",
            "description_sale",
        )
        for marker in (
            "Detail enrichment pending",
            "Pricing requires separate review",
            "Public Blumaq page harvested",
        )
    ):
        reasons.append("internal_enrichment_copy")
    return ";".join(reasons)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Publish eligible products in Odoo")
    parser.add_argument("--limit", type=int, default=0, help="Limit eligible rows to publish")
    args = parser.parse_args()

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
        "description_ecommerce",
        "website_description",
        "description_sale",
    ]
    domain = [
        ("default_code", "!=", MEMBERSHIP_CODE),
        ("sale_ok", "=", True),
        ("active", "=", True),
        ("is_published", "=", False),
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
    eligible_ids = []
    for product in products:
        block = reason_for_block(product)
        eligible = not block or block == "missing_cost_warning"
        if eligible:
            eligible_ids.append(product["id"])
        rows.append(
            {
                "id": product["id"],
                "default_code": product.get("default_code") or "",
                "name": product.get("name") or "",
                "list_price": product.get("list_price") or 0,
                "standard_price": product.get("standard_price") or 0,
                "category": product.get("categ_id") and product["categ_id"][1] or "",
                "eligible": eligible,
                "block_reason": "" if eligible else block,
                "warning": "missing_cost" if block == "missing_cost_warning" else "",
            }
        )

    if args.limit:
        eligible_ids = eligible_ids[: args.limit]

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = OUT_DIR / f"southern_publish_priced_parts_{stamp}.csv"
    with report_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0].keys()) if rows else ["id"])
        writer.writeheader()
        writer.writerows(rows)

    if args.apply and eligible_ids:
        models.execute_kw(
            db,
            uid,
            api_key,
            "product.template",
            "write",
            [eligible_ids, {"is_published": True}],
        )

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "unpublished_checked": len(products),
            "eligible_count": len([row for row in rows if row.get("eligible")]),
            "published_count": len(eligible_ids) if args.apply else 0,
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
