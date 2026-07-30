"""Repair the public Standard Membership service product only."""

from __future__ import annotations

import argparse
import base64
import csv
import io
import struct
import zlib
from datetime import datetime

from odoo_cleanup_published_placeholders import OUT_DIR, connect, execute


MEMBERSHIP_TEMPLATE_ID = 25993
MEMBERSHIP_CODE = "SEC-MEMBERSHIP-STANDARD"
SOUTHERN_WEBSITE_ID = 2


def membership_image() -> str:
    width, height = 1200, 900
    dark = (17, 19, 22)
    panel = (29, 34, 40)
    gold = (239, 187, 46)
    steel = (213, 218, 223)
    white = (247, 244, 234)

    pixels = bytearray()
    for y in range(height):
        row = bytearray([0])
        for x in range(width):
            color = dark
            if y < 22:
                color = gold
            elif 110 <= x <= 1090 and 120 <= y <= 780:
                color = panel
            if 110 <= x <= 1090 and 120 <= y <= 170:
                color = gold
            if 160 <= x <= 1040 and 250 <= y <= 330:
                color = white
            if 160 <= x <= 470 and 405 <= y <= 520:
                color = white
            if 500 <= x <= 760 and 470 <= y <= 515:
                color = steel
            for bullet_y in (585, 635, 685, 735):
                if (x - 176) ** 2 + (y - bullet_y) ** 2 <= 12**2:
                    color = gold
                if 210 <= x <= 930 and bullet_y - 8 <= y <= bullet_y + 14:
                    color = steel
            row.extend(color)
        pixels.extend(row)

    def chunk(name: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + name
            + data
            + struct.pack(">I", zlib.crc32(name + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(pixels), 9))
        + chunk(b"IEND", b"")
    )
    return base64.b64encode(png).decode("ascii")


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
    publish_fields = [
        field
        for field in ("is_published", "website_published")
        if field in fields and not fields[field].get("readonly")
    ]
    product = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "read",
        [[MEMBERSHIP_TEMPLATE_ID]],
        {
            "fields": [
                "id",
                "name",
                "default_code",
                "type",
                "sale_ok",
                "purchase_ok",
                "list_price",
                "active",
                "image_1920",
                "website_id",
                *publish_fields,
            ],
            "context": {"active_test": False},
        },
    )[0]
    if product.get("default_code") != MEMBERSHIP_CODE:
        raise RuntimeError(
            f"Refusing membership repair: expected {MEMBERSHIP_CODE}, got {product.get('default_code')}"
        )

    values = {
        "active": True,
        "type": "service",
        "sale_ok": True,
        "purchase_ok": False,
        "list_price": 25.0,
        "image_1920": membership_image(),
        "website_id": SOUTHERN_WEBSITE_ID,
        "allow_out_of_stock_order": True,
        "show_availability": False,
    }
    if "is_storable" in fields and not fields["is_storable"].get("readonly"):
        values["is_storable"] = False
    if "invoice_policy" in fields and not fields["invoice_policy"].get("readonly"):
        values["invoice_policy"] = "order"
    if "service_tracking" in fields and not fields["service_tracking"].get("readonly"):
        values["service_tracking"] = "no"
    for publish_field in publish_fields:
        values[publish_field] = True

    if args.apply:
        execute(
            models,
            db,
            uid,
            key,
            "product.template",
            "write",
            [[MEMBERSHIP_TEMPLATE_ID], values],
        )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "standard_membership_repair_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Old Published",
                "Had Image",
                "Action",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "Product ID": MEMBERSHIP_TEMPLATE_ID,
                "Internal Reference": MEMBERSHIP_CODE,
                "Old Published": any(bool(product.get(field)) for field in publish_fields),
                "Had Image": bool(product.get("image_1920")),
                "Action": "Updated and published" if args.apply else "Would update and publish",
            }
        )
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "product_id": MEMBERSHIP_TEMPLATE_ID,
            "publish_fields": publish_fields,
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
