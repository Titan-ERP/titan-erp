"""Read-only audit of customer-facing Odoo product content quality."""

from __future__ import annotations

import csv
import re
from collections import Counter
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


INTERNAL_COPY_PATTERNS = {
    "Enrichment pending": re.compile(
        r"detail enrichment pending", re.IGNORECASE
    ),
    "Raw source URL": re.compile(r"https?://", re.IGNORECASE),
    "Internal source label": re.compile(
        r"^\s*(?:sparex|source)\s+source\s*:", re.IGNORECASE | re.MULTILINE
    ),
    "Redundant product label": re.compile(
        r"^\s*product\s*:", re.IGNORECASE | re.MULTILINE
    ),
    "Redundant SKU label": re.compile(
        r"^\s*sku\s*:", re.IGNORECASE | re.MULTILINE
    ),
}

NAME_REPLACEMENTS = {
    "stabalizer": "stabilizer",
    "seperator": "separator",
    "rivit": "rivet",
    "hyydr": "hydraulic",
}


def chunks(values, size=500):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def main() -> int:
    db, uid, key, models = connect()
    fields = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "fields_get",
        [],
        {"attributes": ["type", "readonly"]},
    )
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
    read_fields = [
        "id",
        "default_code",
        "name",
        "public_categ_ids",
        *description_fields,
    ]
    product_ids = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search",
        [[("active", "=", True), (published_field, "=", True)]],
        {"limit": 0, "order": "id"},
    )
    products = []
    for id_chunk in chunks(product_ids):
        products.extend(
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "read",
                [id_chunk],
                {"fields": read_fields},
            )
        )

    rows = []
    issue_counts = Counter()
    for product in products:
        name = clean_text(product.get("name"))
        descriptions = {
            field: str(product.get(field) or "")
            for field in description_fields
        }
        combined = "\n".join(descriptions.values())
        issues = []
        for issue, pattern in INTERNAL_COPY_PATTERNS.items():
            if pattern.search(combined):
                issues.append(issue)
                issue_counts[issue] += 1
        lower_name = name.lower()
        for typo, replacement in NAME_REPLACEMENTS.items():
            if typo in lower_name:
                issue = f"Name typo: {typo} -> {replacement}"
                issues.append(issue)
                issue_counts[issue] += 1
        code = clean_text(product.get("default_code"))
        if name.lower() == code.lower() and code:
            issues.append("Name is only the internal reference")
            issue_counts["Name is only the internal reference"] += 1
        search_reference = code
        if code.upper().startswith("SEC-"):
            search_reference = ""
        elif code.upper().startswith("BLQ-"):
            search_reference = code.split("-", 1)[1]
        elif code.upper().startswith(("PAR-", "PRO-", "TMP-")):
            search_reference = ""
        if (
            search_reference
            and search_reference.lower() not in name.lower()
        ):
            issues.append("Reference absent from visible name")
            issue_counts["Reference absent from visible name"] += 1
        if (
            len(name.split()) <= 2
            and search_reference
            and search_reference.lower() not in name.lower()
        ):
            issues.append("Weak short name")
            issue_counts["Weak short name"] += 1
        if not any(clean_text(value) for value in descriptions.values()):
            issues.append("Missing customer description")
            issue_counts["Missing customer description"] += 1
        if issues:
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": code,
                    "Name": name,
                    "Issues": "; ".join(issues),
                    **{
                        field: clean_text(product.get(field))
                        for field in description_fields
                    },
                }
            )

    missing_images = (
        execute(
            models,
            db,
            uid,
            key,
            "product.template",
            "search_count",
            [
                [
                    ("active", "=", True),
                    (published_field, "=", True),
                    ("image_1920", "=", False),
                ]
            ],
        )
        if "image_1920" in fields
        else None
    )
    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "storefront_content_quality_audit_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    report_fields = [
        "Product ID",
        "Internal Reference",
        "Name",
        "Issues",
        *description_fields,
    ]
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=report_fields)
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "published_products": len(products),
            "description_fields": description_fields,
            "products_with_content_issues": len(rows),
            "missing_images": missing_images,
            "issue_counts": dict(sorted(issue_counts.items())),
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

