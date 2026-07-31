"""Resolve verified duplicate published product names using OEM references."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


UPDATES = {
    6684: "Alternator - OEM AT173624",
    6685: "Alternator - OEM TY24485",
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db, uid, key, models = connect()
    products = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "read",
        [sorted(UPDATES)],
        {"fields": ["id", "default_code", "barcode", "name"]},
    )
    rows = []
    for product in products:
        expected_oem = product.get("default_code") or product.get("barcode") or ""
        new_name = UPDATES[product["id"]]
        if expected_oem and expected_oem not in new_name:
            raise RuntimeError(
                f"OEM guard failed for product {product['id']}: {expected_oem}"
            )
        if args.apply and product.get("name") != new_name:
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "write",
                [[product["id"]], {"name": new_name}],
            )
        rows.append(
            {
                "Product ID": product["id"],
                "Internal Reference": expected_oem,
                "Old Name": product.get("name") or "",
                "New Name": new_name,
                "Status": "Updated" if args.apply else "Would update",
            }
        )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "published_name_disambiguation_"
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
