"""Clean stale public product copy after Internal Reference corrections.

This guarded cleanup fixes generic/duplicate-looking published names by adding
the current reference, and removes old PAR-* text from storefront descriptions.
It skips exact hand-written storefront copy fixes.
"""

from __future__ import annotations

import argparse
import csv
import html
import re
from datetime import datetime

from odoo_clean_storefront_product_copy import UPDATES
from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


GENERIC_NAMES = {
    "adapter",
    "alternator",
    "bearing",
    "belt",
    "bolt",
    "filter",
    "fitting",
    "gasket",
    "hose",
    "kit",
    "nut",
    "part",
    "pin",
    "pump",
    "seal",
    "sensor",
    "starter",
    "switch",
    "washer",
}
OLD_PAR = re.compile(r"\bPAR-\d+\b", re.IGNORECASE)


def chunks(values: list[int], size: int = 500):
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def clean_text(value) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def visible_reference(code: str) -> str:
    code = clean_text(code)
    if code.upper().startswith(("SEC-", "PAR-", "PRO-", "TMP-")):
        return ""
    if code.upper().startswith("BLQ-"):
        return code.split("-", 1)[1]
    return code


def display_name(name: str, code: str) -> str:
    ref = visible_reference(code)
    if not ref:
        return name
    if ref.lower() in name.lower():
        return name
    if code.upper().startswith("S."):
        return f"{name} - Sparex {ref}"
    if code.upper().startswith("BLQ-"):
        return f"{name} - Blumaq {ref}"
    return f"{name} - OEM {ref}"


def website_copy(name: str, code: str) -> str:
    ref = visible_reference(code) or code
    return (
        '<div class="se-product-summary">'
        f"<p>{html.escape(name)} identified by reference {html.escape(ref)}.</p>"
        "<ul>"
        f"<li><strong>Reference:</strong> {html.escape(code)}</li>"
        "</ul>"
        "<p>Confirm dimensions, fitment, and application before ordering.</p>"
        "</div>"
    )


def sale_copy(name: str, code: str) -> str:
    ref = visible_reference(code) or code
    return (
        f"{name} identified by reference {ref}. "
        "Confirm dimensions, fitment, and application before ordering."
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db, uid, key, models = connect()
    fields = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "fields_get",
        [],
        {"attributes": ["readonly"]},
    )
    published_field = (
        "website_published" if "website_published" in fields else "is_published"
    )
    description_fields = [
        field
        for field in (
            "description_ecommerce",
            "website_description",
            "description_sale",
        )
        if field in fields
    ]

    ids = execute(
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
    for id_chunk in chunks(ids):
        products.extend(
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "read",
                [id_chunk],
                {
                    "fields": [
                        "id",
                        "default_code",
                        "name",
                        *description_fields,
                    ]
                },
            )
        )

    name_counts = {}
    for product in products:
        key_name = clean_text(product.get("name")).lower()
        name_counts[key_name] = name_counts.get(key_name, 0) + 1

    rows = []
    for product in products:
        product_id = product["id"]
        if product_id in UPDATES:
            continue
        code = clean_text(product.get("default_code"))
        name = clean_text(product.get("name"))
        ref = visible_reference(code)
        descriptions = "\n".join(str(product.get(field) or "") for field in description_fields)
        name_key = name.lower()
        needs_name = bool(
            ref
            and ref.lower() not in name_key
            and (
                OLD_PAR.search(descriptions)
                or name_counts.get(name_key, 0) > 1
                or name_key in GENERIC_NAMES
                or len(name.split()) <= 2
            )
        )
        needs_copy = bool(OLD_PAR.search(descriptions))
        if not needs_name and not needs_copy:
            continue
        new_name = display_name(name, code) if needs_name else name
        values = {}
        if new_name != name:
            values["name"] = new_name
        if needs_copy:
            if "description_ecommerce" in description_fields:
                values["description_ecommerce"] = website_copy(new_name, code)
            if "website_description" in description_fields:
                values["website_description"] = website_copy(new_name, code)
            if "description_sale" in description_fields:
                values["description_sale"] = sale_copy(new_name, code)
        if args.apply and values:
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "write",
                [[product_id], values],
            )
        rows.append(
            {
                "Product ID": product_id,
                "Internal Reference": code,
                "Old Name": name,
                "New Name": new_name,
                "Changed Fields": "; ".join(sorted(values)),
                "Status": "Updated" if args.apply else "Would update",
            }
        )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "published_reference_copy_cleanup_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Old Name",
                "New Name",
                "Changed Fields",
                "Status",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(rows),
            "updated": len(rows) if args.apply else 0,
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
