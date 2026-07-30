"""Apply verified customer-facing copy fixes to published products."""

from __future__ import annotations

import argparse
import csv
import html
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


UPDATES = {
    3708: {
        "code": "S.57887,57887,32/42",
        "name": "PTO Seal Kit - Sparex S.57887 (32/42)",
        "summary": "Sparex PTO seal kit, reference S.57887, for 32/42 applications.",
        "fitment": "Confirm PTO model, dimensions, and seal application before ordering.",
    },
    6329: {
        "code": "01010-51680",
        "name": "Hex Bolt 16 x 80 mm - OEM 01010-51680",
        "summary": "Replacement 16 x 80 mm hex bolt, OEM reference 01010-51680.",
        "fitment": "Confirm thread pitch, grade, and application before ordering.",
    },
    6330: {
        "code": "010100-51885",
        "name": "Hex Bolt 18 x 85 mm - OEM 010100-51885",
        "summary": "Replacement 18 x 85 mm hex bolt, OEM reference 010100-51885.",
        "fitment": "Confirm thread pitch, grade, and application before ordering.",
    },
    6387: {
        "code": "S.4792",
        "name": "Bearing Kit - Sparex S.4792",
        "summary": "Sparex replacement bearing kit, reference S.4792.",
        "fitment": "Confirm bearing dimensions and equipment fitment before ordering.",
    },
    6559: {
        "code": "S.128481",
        "name": '1 3/8" Shear Pin Yoke - Sparex S.128481',
        "summary": 'Sparex 1 3/8" shear pin yoke, reference S.128481.',
        "fitment": "Confirm shaft size, connection dimensions, and driveline fitment before ordering.",
    },
    6684: {
        "code": "AT173624",
        "name": "Alternator - OEM AT173624",
        "summary": "Replacement alternator identified by OEM reference AT173624.",
        "fitment": "Confirm voltage, amperage, mounting, and machine fitment before ordering.",
    },
    6685: {
        "code": "TY24485",
        "name": "Alternator - OEM TY24485",
        "summary": "Replacement alternator identified by OEM reference TY24485.",
        "fitment": "Confirm voltage, amperage, mounting, and machine fitment before ordering.",
    },
    6647: {
        "code": "S.40160",
        "name": "Power Steering Pump Filter - Sparex S.40160",
        "summary": "Sparex replacement power steering pump filter, reference S.40160.",
        "fitment": "Confirm filter dimensions and steering-system fitment before ordering.",
    },
    6671: {
        "code": "S.154482",
        "name": "Hydraulic Filter - Sparex S.154482",
        "summary": "Sparex replacement hydraulic filter, reference S.154482.",
        "fitment": "Confirm filter dimensions and hydraulic-system fitment before ordering.",
    },
    6717: {
        "code": "119225-52102",
        "name": "Feed Pump - OEM 119225-52102",
        "summary": "Replacement feed pump identified by OEM reference 119225-52102.",
        "fitment": "Confirm engine model and serial-number fitment before ordering.",
    },
    6719: {
        "code": "023-69197",
        "name": 'Hex Castle Nut 1" - OEM 023-69197',
        "summary": 'Replacement 1" gearbox castle nut, OEM reference 023-69197.',
        "fitment": "Confirm thread size, pitch, and gearbox application before ordering.",
    },
    6801: {
        "code": "02781-00422",
        "name": "Hydraulic Union - OEM 02781-00422",
        "summary": "Replacement hydraulic union, OEM reference 02781-00422.",
        "fitment": "Confirm thread type, size, and hydraulic application before ordering.",
    },
    6936: {
        "code": "4135550",
        "name": "Coolant Water Pump - OEM 4135550",
        "summary": "Replacement coolant water pump identified by OEM reference 4135550.",
        "fitment": "Confirm engine model and serial-number fitment before ordering.",
    },
    10516: {
        "code": "02896-21009",
        "name": "O-Ring - OEM 02896-21009",
        "summary": "Replacement O-ring identified by OEM reference 02896-21009.",
        "fitment": "Confirm material, dimensions, and application before ordering.",
    },
    11131: {
        "code": "S.118682",
        "name": "Massey Ferguson Gray Spray Paint - Sparex S.118682",
        "summary": "Sparex Massey Ferguson gray spray paint, reference S.118682.",
        "fitment": "Verify color match and surface preparation requirements before use.",
    },
    11219: {
        "code": "016-TW694",
        "name": 'Mower Tail Wheel 6 x 9 in, 4-Bolt - OEM 016-TW694',
        "summary": 'Replacement 6 x 9 inch mower tail wheel with four bolt holes on a 5-inch circle.',
        "fitment": "Confirm hub pattern, dimensions, and mower application before ordering.",
    },
    11234: {
        "code": "006016642U91",
        "name": "Engine Oil Filter - OEM 006016642U91",
        "summary": "Replacement engine oil filter, OEM reference 006016642U91.",
        "fitment": "Confirm filter dimensions and engine fitment before ordering.",
    },
    11292: {
        "code": "S.11637",
        "name": "Stabilizer Pin, Pin ø19mm x 90mm - Sparex S.11637",
        "summary": "Replacement stabilizer pin with 19 mm pin diameter and 90 mm length.",
        "fitment": "Confirm dimensions and application before ordering.",
    },
    26042: {
        "code": "BLQ-3652574",
        "name": "Pin - Blumaq 3652574",
        "summary": "Blumaq replacement pin suitable for Caterpillar equipment.",
        "fitment": "Confirm machine model, serial number, and reference 3652574 before ordering.",
    },
    26043: {
        "code": "BLQ-8W2842",
        "name": "Cap Assembly - Blumaq 8W2842",
        "summary": "Blumaq replacement cap assembly suitable for Caterpillar equipment.",
        "fitment": "Confirm machine model, serial number, and reference 8W2842 before ordering.",
    },
    26056: {
        "code": "BLQ-BQ105150925",
        "name": "Filter - Replaces 1046931Q - Blumaq BQ105150925",
        "summary": "Blumaq replacement filter cross-referenced to 1046931Q.",
        "fitment": "Confirm filter dimensions and equipment fitment before ordering.",
    },
    26065: {
        "code": "BLQ-BQ10260444",
        "name": "Filter - Replaces 1041426BQ - Blumaq BQ10260444",
        "summary": "Blumaq replacement filter cross-referenced to 1041426BQ.",
        "fitment": "Confirm filter dimensions and equipment fitment before ordering.",
    },
    26079: {
        "code": "BLQ-5848057",
        "name": "Bearing - Blumaq 5848057",
        "summary": "Blumaq replacement bearing suitable for Caterpillar equipment.",
        "fitment": "Confirm machine model, serial number, and reference 5848057 before ordering.",
    },
    26092: {
        "code": "BLQ-BQ1094874",
        "name": "Filter - Replaces 0813957BQ - Blumaq BQ1094874",
        "summary": "Blumaq replacement filter cross-referenced to 0813957BQ.",
        "fitment": "Confirm filter dimensions and equipment fitment before ordering.",
    },
    26098: {
        "code": "BLQ-BQ1081228",
        "name": "Filter - Replaces 1106331BQ - Blumaq BQ1081228",
        "summary": "Blumaq replacement filter cross-referenced to 1106331BQ.",
        "fitment": "Confirm filter dimensions and equipment fitment before ordering.",
    },
    26111: {
        "code": "BLQ-BQ101200891",
        "name": "Filter - Replaces 1129689BQ - Blumaq BQ101200891",
        "summary": "Blumaq replacement filter cross-referenced to 1129689BQ.",
        "fitment": "Confirm filter dimensions and equipment fitment before ordering.",
    },
}


def website_description(update):
    return (
        '<div class="se-product-summary">'
        f"<p>{html.escape(update['summary'])}</p>"
        "<ul>"
        f"<li><strong>Reference:</strong> {html.escape(update['code'])}</li>"
        "</ul>"
        f"<p>{html.escape(update['fitment'])}</p>"
        "</div>"
    )


def sale_description(update):
    return (
        f"{update['summary']} Reference: {update['code']}. "
        f"{update['fitment']}"
    )


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
        {
            "fields": [
                "id",
                "default_code",
                "name",
                "website_description",
                "description_sale",
            ]
        },
    )
    rows = []
    for product in products:
        update = UPDATES[product["id"]]
        if product.get("default_code") != update["code"]:
            raise RuntimeError(
                f"Reference guard failed for product {product['id']}: "
                f"{product.get('default_code')} != {update['code']}"
            )
        values = {
            "name": update["name"],
            "website_description": website_description(update),
            "description_sale": sale_description(update),
        }
        if args.apply:
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "write",
                [[product["id"]], values],
            )
        rows.append(
            {
                "Product ID": product["id"],
                "Internal Reference": update["code"],
                "Old Name": product.get("name") or "",
                "New Name": update["name"],
                "Status": "Updated" if args.apply else "Would update",
            }
        )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "storefront_product_copy_cleanup_"
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
