"""Enforce publication for verified Southern equipment opportunities.

The default mode is read-only. ``--apply`` publishes records that pass every gate
and unpublishes records that no longer pass. It never edits prices, grades, source
facts, images, seller data, or record existence. Comparable analysis is advisory:
grade and comp availability never prevent an otherwise complete listing.
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

try:
    from scripts.odoo_repair_facebook_brokerage_listings import call, connect
except ModuleNotFoundError:
    from odoo_repair_facebook_brokerage_listings import call, connect


TARGET_COMPANY_ID = 2
VERIFIED_STATUSES = {
    "published",
    "inquiry_received",
    "seller_confirmed",
    "under_negotiation",
    "under_contract",
}


def facebook_source_matches(record: dict[str, Any]) -> bool:
    if record.get("source") != "facebook_marketplace":
        return True
    listing_id = str(record.get("source_listing_id") or "").strip()
    source_url = str(record.get("source_url") or "").strip()
    if not listing_id.isdigit():
        return False
    return bool(
        re.fullmatch(
            rf"https://(?:www\.)?facebook\.com/marketplace/item/{re.escape(listing_id)}/?",
            source_url,
            flags=re.IGNORECASE,
        )
    )


def publication_blockers(record: dict[str, Any]) -> list[str]:
    checks = (
        ("wrong_company", record.get("company_id") in (TARGET_COMPANY_ID, [TARGET_COMPANY_ID, "Southern Equipment Company (Laurel)"])),
        ("unverified_status", record.get("public_status") in VERIFIED_STATUSES),
        ("missing_year", int(record.get("year") or 0) > 0),
        ("missing_model", bool(str(record.get("model") or "").strip())),
        ("missing_public_price", float(record.get("public_price") or 0) > 0),
        ("missing_public_region", bool(str(record.get("public_region") or "").strip())),
        ("missing_public_description", bool(record.get("public_description"))),
        ("missing_verification_note", bool(str(record.get("verification_note") or "").strip())),
        ("missing_approved_image", bool(record.get("image_present"))),
        ("photo_rights_unconfirmed", bool(record.get("photo_rights_confirmed"))),
        ("missing_photo_source_note", bool(str(record.get("photo_source_note") or "").strip())),
        ("source_link_mismatch", facebook_source_matches(record)),
    )
    return [name for name, passed in checks if not passed]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Publish eligible records.")
    args = parser.parse_args()

    connection = connect()
    fields = [
        "id",
        "public_title",
        "company_id",
        "website_published",
        "public_status",
        "grade",
        "comp_count",
        "year",
        "model",
        "hours",
        "public_price",
        "public_region",
        "public_description",
        "verification_note",
        "photo_rights_confirmed",
        "photo_source_note",
        "source",
        "source_listing_id",
        "source_url",
    ]
    records = call(
        connection,
        "search_read",
        [[("company_id", "=", TARGET_COMPANY_ID)]],
        {"fields": fields, "limit": 10000, "order": "id"},
    )
    image_ids = set(
        call(
            connection,
            "search",
            [[("company_id", "=", TARGET_COMPANY_ID), ("image_1920", "!=", False)]],
            {"limit": 10000},
        )
    )
    for record in records:
        record["image_present"] = record["id"] in image_ids

    eligible = [record for record in records if not publication_blockers(record)]
    to_publish = [record for record in eligible if not record.get("website_published")]
    to_unpublish = [
        record
        for record in records
        if record.get("website_published") and publication_blockers(record)
    ]
    blocked = [
        {
            "id": record["id"],
            "title": record.get("public_title"),
            "blockers": publication_blockers(record),
        }
        for record in records
        if publication_blockers(record)
    ]

    published = 0
    unpublished = 0
    if args.apply and to_publish:
        publish_ids = [record["id"] for record in to_publish]
        if not call(connection, "write", [publish_ids, {"website_published": True}]):
            raise RuntimeError("Odoo rejected the publication write.")
        published = len(publish_ids)
    if args.apply and to_unpublish:
        unpublish_ids = [record["id"] for record in to_unpublish]
        if not call(connection, "write", [unpublish_ids, {"website_published": False}]):
            raise RuntimeError("Odoo rejected the unpublication write.")
        unpublished = len(unpublish_ids)

    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                "scanned": len(records),
                "eligible": len(eligible),
                "eligible_ids": [record["id"] for record in eligible],
                "to_publish_ids": [record["id"] for record in to_publish],
                "to_unpublish_ids": [record["id"] for record in to_unpublish],
                "published": published,
                "unpublished": unpublished,
                "blocked": len(blocked),
                "blocked_records": blocked,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
