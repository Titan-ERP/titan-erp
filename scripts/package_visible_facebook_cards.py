"""Package user-authorized visible Facebook Marketplace cards for Odoo intake."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime
from pathlib import Path


FIELDS = [
    "Target Company ID", "Target Company", "Source", "Source Listing ID",
    "Canonical Source URL", "Original Facebook Link", "Standardized Title", "Safe Public Description",
    "Seller Ask", "Public Price", "Currency", "Restricted Exact Location",
    "Public Region", "Availability Status", "Availability Evidence",
    "Availability Checked Timestamp", "Equipment Type", "Manufacturer", "Model",
    "Year", "Hours", "Seller Name", "Seller Phone", "Seller Email", "VIN/Serial",
    "Raw Capture Text", "Capture Run ID", "Capture Date", "Capture Provenance",
    "Image Local Path or URL", "Photo Rights Confirmed",
    "Photo Source/License Note", "Representative/Generic Image",
    "Representative Image Category", "Public Status", "Published on Website",
    "Publish Ready", "Publish Blockers", "Review Notes",
]

REGIONS = {
    "AL": "Alabama", "GA": "Georgia", "KY": "Kentucky", "LA": "Louisiana",
    "MS": "Mississippi", "NC": "North Carolina", "SC": "South Carolina",
    "TN": "Tennessee", "TX": "Texas",
}


def package(cards: list[dict], run_id: str, checked_at: str) -> list[dict[str, object]]:
    seen = set()
    rows = []
    for card in cards:
        source_id = str(card["id"]).strip()
        if not re.fullmatch(r"\d+", source_id):
            raise ValueError(f"Invalid Facebook listing ID: {source_id!r}")
        if source_id in seen:
            raise ValueError(f"Duplicate Facebook listing ID: {source_id}")
        seen.add(source_id)
        ask = float(card["price"])
        if ask <= 1:
            raise ValueError(f"{source_id}: non-credible advertised price {ask}")
        location = str(card["location"]).strip()
        if card.get("detail_verified") is not True:
            raise ValueError(f"{source_id}: visible detail page was not verified")
        detail_id = str(card.get("detail_id", "")).strip()
        detail_title = " ".join(str(card.get("detail_title", "")).split())
        detail_location = " ".join(str(card.get("detail_location", "")).split())
        detail_price = float(card.get("detail_price") or 0)
        detail_checked_at = str(card.get("detail_checked_at", "")).strip()
        datetime.fromisoformat(detail_checked_at)
        if detail_id != source_id:
            raise ValueError(f"{source_id}: detail-page listing ID conflict: {detail_id!r}")
        if detail_title.casefold() != " ".join(str(card["title"]).split()).casefold():
            raise ValueError(f"{source_id}: detail-page title conflicts with search card")
        if abs(detail_price - ask) >= 0.01:
            raise ValueError(f"{source_id}: detail-page price conflicts with search card")
        if detail_location.casefold() != " ".join(location.split()).casefold():
            raise ValueError(f"{source_id}: detail-page location conflicts with search card")
        state = location.rsplit(",", 1)[-1].strip().upper() if "," in location else ""
        region = REGIONS.get(state, "")
        if not region:
            raise ValueError(f"{source_id}: unsupported or missing public region")
        title = str(card["title"]).strip()
        standardized_title = str(card.get("standardized_title") or title).strip()
        equipment_type = str(card["equipment_type"]).strip()
        description = " ".join(str(card.get("description") or "").split())
        seller_name = " ".join(str(card.get("seller_name") or "").split())
        raw = f"{title}; ${ask:,.0f}; {location}."
        if description:
            raw = f"{raw} Seller's description: {description}"
        blockers = [
            "availability_not_verified_available",
            "seller_identity_not_captured",
            "photo_rights_not_confirmed",
            "representative_image_disclosure_required",
        ]
        if not card.get("year"):
            blockers.append("missing_year")
        if not card.get("manufacturer"):
            blockers.append("missing_manufacturer")
        if not card.get("model"):
            blockers.append("missing_model")
        rows.append({
            "Target Company ID": 2,
            "Target Company": "Southern Equipment Company (Laurel)",
            "Source": "facebook_marketplace",
            "Source Listing ID": source_id,
            "Canonical Source URL":
                f"https://www.facebook.com/marketplace/item/{source_id}/",
            "Original Facebook Link": str(card.get("shared_url") or "").strip(),
            "Standardized Title": standardized_title,
            "Safe Public Description":
                f"Broker-assisted sourced opportunity: {standardized_title}. Seller ask captured "
                f"at ${ask:,.0f} USD. General location: {region}. Availability, "
                "condition, ownership, and specifications require verification.",
            "Seller Ask": f"{ask:.2f}",
            "Public Price": f"{ask * 1.05:.2f}",
            "Currency": "USD",
            "Restricted Exact Location": f"Marketplace approximate: {location}",
            "Public Region": region,
            "Availability Status": "unverified",
            "Availability Evidence":
                "Search card and visible Marketplace detail page agreed during this cycle.",
            "Availability Checked Timestamp": detail_checked_at,
            "Equipment Type": equipment_type,
            "Manufacturer": card.get("manufacturer", ""),
            "Model": card.get("model", ""),
            "Year": card.get("year", ""),
            "Hours": card.get("hours", ""),
            "Seller Name": seller_name,
            "Seller Phone": "",
            "Seller Email": "",
            "VIN/Serial": "",
            "Raw Capture Text": raw,
            "Capture Run ID": run_id,
            "Capture Date": checked_at[:10],
            "Capture Provenance":
                "User-authorized visible in-app browser search card and detail page.",
            "Image Local Path or URL": "",
            "Photo Rights Confirmed": "False",
            "Photo Source/License Note":
                "Seller-image reuse rights not established; use Southern-owned representative image.",
            "Representative/Generic Image": "True",
            "Representative Image Category": equipment_type,
            "Public Status": "verification_in_progress",
            "Published on Website": "False",
            "Publish Ready": "False",
            "Publish Blockers": ";".join(blockers),
            "Review Notes":
                "Visible search-card and detail-page identity fields agreed. Seller identity, "
                "ownership, condition, and specifications still require human verification.",
        })
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--checked-at", required=True)
    args = parser.parse_args()
    datetime.fromisoformat(args.checked_at)
    cards = json.loads(args.input.read_text(encoding="utf-8"))
    rows = package(cards, args.run_id, args.checked_at)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"INPUT={len(cards)}")
    print(f"PACKAGED={len(rows)}")
    print(f"OUTPUT={args.output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
