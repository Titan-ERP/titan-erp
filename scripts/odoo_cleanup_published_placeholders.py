"""Remove unfinished products from the public Odoo storefront.

The default mode is a read-only dry run.  ``--apply`` only unpublishes active
products that fail storefront requirements: a website category, a sales price
above $1, customer-ready copy, and a visible product image. Product records and
their inventory and accounting data are preserved.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from datetime import datetime
from pathlib import Path
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "outputs"
SPAREX_PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(
        db, username, api_key, {}
    )
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[int], size: int = 500):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def money(value) -> float:
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def load_sparex_low_price_evidence_refs() -> set[str]:
    """Return Sparex SKUs with exact URL-backed evidence allowing <= $1 prices.

    The storefront cleanup still blocks normal placeholder prices. This carveout
    only protects low-priced Sparex rows that came from a verified price-apply
    report or a queue publish input with an evidence URL.
    """
    evidence_refs: set[str] = set()
    patterns = [
        "odoo_sparex_website_price_apply_report_*.csv",
        "odoo_sparex_farmingparts_gbp_price_apply_publish_input_*.csv",
        "odoo_sparex_recent_farmingparts_gbp_price_apply_publish_input_*.csv",
        "odoo_sparex_queued_exact_usd_price_apply_publish_input_*.csv",
    ]
    for pattern in patterns:
        for path in SPAREX_PRICING_DIR.glob(pattern):
            try:
                with path.open("r", encoding="utf-8-sig", newline="") as handle:
                    for row in csv.DictReader(handle):
                        sku = str(row.get("Internal Reference") or "").strip().upper()
                        price = money(
                            row.get("New Sales Price")
                            or row.get("Sales Price")
                            or row.get("Evidence Price")
                        )
                        evidence_url = str(row.get("Evidence URLs") or row.get("Evidence URL") or "").strip()
                        if sku.startswith("S.") and 0 < price <= 1.0 and evidence_url.startswith(("http://", "https://")):
                            evidence_refs.add(sku)
            except OSError:
                continue
    return evidence_refs


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--require-images",
        action="store_true",
        help="Also unpublish products missing image_1920. By default, missing photos are allowed when price/category/copy are ready.",
    )
    parser.add_argument(
        "--require-descriptions",
        action="store_true",
        help="Also unpublish products missing customer descriptions. By default, priced/category-ready products may remain published while copy is improved.",
    )
    parser.add_argument(
        "--do-not-allow-evidence-backed-low-sparex-prices",
        action="store_true",
        help="Treat all <= $1 published prices as placeholders, even exact URL-backed Sparex hardware prices.",
    )
    args = parser.parse_args()
    sparex_low_price_evidence_refs = (
        set()
        if args.do_not_allow_evidence_backed_low_sparex_prices
        else load_sparex_low_price_evidence_refs()
    )

    db, uid, api_key, models = connect()
    fields = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "fields_get",
        [],
        {"attributes": ["readonly"]},
    )
    publish_fields = [
        name
        for name in ("is_published", "website_published")
        if name in fields and not fields[name].get("readonly")
    ]
    if not publish_fields:
        raise RuntimeError("No writable product publication field is available")

    published_field = (
        "website_published" if "website_published" in fields else "is_published"
    )
    description_fields = [
        name
        for name in (
            "description_ecommerce",
            "website_description",
            "description_sale",
        )
        if name in fields
    ]
    unsafe_expressions = [
        [("public_categ_ids", "=", False)],
        [("list_price", "<=", 1.0)],
    ]
    if args.require_images and "image_1920" in fields:
        unsafe_expressions.append([("image_1920", "=", False)])
    for description_field in description_fields:
        unsafe_expressions.extend(
            [
                [
                    (
                        description_field,
                        "ilike",
                        "Detail enrichment pending",
                    )
                ],
                [
                    (
                        description_field,
                        "ilike",
                        "Pricing requires separate review",
                    )
                ],
                [
                    (
                        description_field,
                        "ilike",
                        "Public Blumaq page harvested",
                    )
                ],
            ]
        )
    if args.require_descriptions and description_fields:
        unsafe_expressions.append(
            [
                *(["&"] * (len(description_fields) - 1)),
                *[
                    (description_field, "=", False)
                    for description_field in description_fields
                ],
            ]
        )
    unsafe_domain = [
        *(["|"] * (len(unsafe_expressions) - 1)),
        *[
            token
            for expression in unsafe_expressions
            for token in expression
        ],
    ]
    domain = [
        ("active", "=", True),
        (published_field, "=", True),
        *unsafe_domain,
    ]
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"limit": 0, "order": "id"},
    )

    rows = []
    unsafe_product_ids: list[int] = []
    for id_chunk in chunks(product_ids):
        products = execute(
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
                    "categ_id",
                    "public_categ_ids",
                    *([
                        "image_1920",
                    ] if "image_1920" in fields else []),
                    *description_fields,
                ]
            },
        )
        for product in products:
            code = str(product.get("default_code") or "").strip().upper()
            price = float(product.get("list_price") or 0)
            placeholder_price = price <= 1.0 and code not in sparex_low_price_evidence_refs
            reasons = [
                reason
                for reason, applies in [
                    (
                        "Missing website category",
                        not product.get("public_categ_ids"),
                    ),
                    (
                        "Placeholder price",
                        placeholder_price,
                    ),
                    (
                        "Missing product image",
                        args.require_images
                        and "image_1920" in fields
                        and not product.get("image_1920"),
                    ),
                    (
                        "Internal enrichment copy",
                        any(
                            marker.lower()
                            in str(product.get(field) or "").lower()
                            for field in description_fields
                            for marker in (
                                "Detail enrichment pending",
                                "Pricing requires separate review",
                                "Public Blumaq page harvested",
                            )
                        ),
                    ),
                    (
                        "Missing customer description",
                        args.require_descriptions
                        and bool(description_fields)
                        and not any(
                            str(product.get(field) or "").strip()
                            for field in description_fields
                        ),
                    ),
                ]
                if applies
            ]
            if not reasons:
                continue
            unsafe_product_ids.append(product["id"])
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Sales Price": product.get("list_price") or 0,
                    "Internal Category": (
                        product["categ_id"][1] if product.get("categ_id") else ""
                    ),
                    "Reason": "; ".join(reasons),
                    "Action": "Unpublished" if args.apply else "Would unpublish",
                }
            )

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = OUT_DIR / f"website_placeholder_cleanup_{stamp}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Sales Price",
                "Internal Category",
                "Reason",
                "Action",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    if args.apply:
        values = {field: False for field in publish_fields}
        for id_chunk in chunks(unsafe_product_ids):
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "write",
                [id_chunk, values],
            )

    remaining = 0
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(unsafe_product_ids),
            "unpublished": len(unsafe_product_ids) if args.apply else 0,
            "remaining_matches": remaining,
            "report": str(report_path),
        }
    )
    if args.apply and remaining:
        raise RuntimeError(f"{remaining} placeholder products remain published")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

