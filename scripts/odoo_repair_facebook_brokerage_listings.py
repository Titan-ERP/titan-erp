"""Repair and optionally publish staged Facebook equipment opportunities in Odoo.

This script uses only the authorized local sourcing package and Odoo XML-RPC.
It never accesses Facebook. By default it is read-only; writes require --apply.
Publishing additionally requires --publish.
"""

from __future__ import annotations

import argparse
import base64
import csv
import html
import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
DEFAULT_INPUT = (
    ROOT
    / "outputs"
    / "facebook_marketplace_deals"
    / "facebook-sourcing-hardened-20260726.csv"
)
IMAGE_DIR = (
    ROOT
    / "southern_equipment_brokerage"
    / "static"
    / "src"
    / "img"
    / "representative"
)
MODEL = "southern.equipment.listing"
TARGET_COMPANY_ID = 2
TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
ASSET_NOTE = (
    "Representative image generated for Southern Equipment on 2026-07-26. "
    "Authorized for Southern Equipment website use; not the actual seller machine."
)


def load_env(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


def connect():
    load_env(ENV_PATH)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise RuntimeError("Odoo authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def call(connection, method: str, args: list, kwargs: dict | None = None):
    db, uid, api_key, models = connection
    return models.execute_kw(db, uid, api_key, MODEL, method, args, kwargs or {})


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise RuntimeError("The hardened sourcing file is empty.")
    return rows


def value(row: dict[str, str], *names: str) -> str:
    for name in names:
        text = (row.get(name) or "").strip()
        if text:
            return text
    return ""


def number(text: object) -> float:
    cleaned = "".join(
        char for char in str(text or "") if char.isdigit() or char in ".-"
    )
    return float(cleaned) if cleaned else 0.0


def field_by_label(fields: dict, label: str) -> str | None:
    matches = [name for name, spec in fields.items() if spec.get("string") == label]
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous live fields for label {label!r}: {matches}")
    return matches[0] if matches else None


def broad_region(row: dict[str, str]) -> str:
    explicit = value(row, "Public Region")
    if explicit:
        return explicit
    exact = value(row, "Restricted Exact Location", "Seller Exact Location", "Location")
    state = exact.rsplit(",", 1)[-1].strip().upper() if "," in exact else ""
    return {
        "AL": "Alabama",
        "GA": "Georgia",
        "KY": "Kentucky",
        "NC": "North Carolina",
        "SC": "South Carolina",
        "TN": "Tennessee",
    }.get(state, "")


def public_description(row: dict[str, str], region: str) -> str:
    title = html.escape(
        value(
            row,
            "Standardized Title",
            "Public Title",
            "Equipment Name",
            "Opportunity",
        )
    )
    hours = number(value(row, "Hours"))
    hours_text = f" with {hours:,.0f} reported hours" if hours else ""
    region_text = f" located in {html.escape(region)}" if region else ""
    return (
        f"<p>Broker-assisted opportunity for a {title}{hours_text}{region_text}. "
        "Southern Equipment is coordinating availability, condition, inspection, "
        "and transaction details. The image is representative and may not depict "
        "the seller's actual machine.</p>"
    )


def image_path(row: dict[str, str]) -> Path:
    title = value(row, "Standardized Title", "Public Title", "Equipment Name").lower()
    equipment_type = value(row, "Equipment Type").lower()
    source_id = value(row, "Source Listing ID", "Equipment ID")
    variant = int(source_id[-4:] or "0") % 3
    if "package/bundle" in title or "bundle" in title:
        filename = "equipment-bundle.png"
    elif "telehandler" in equipment_type:
        filename = "telehandler.png"
    elif "mini excavator" in equipment_type:
        filename = "mini-excavator.png"
    elif "dozer" in equipment_type:
        filename = ("dozer.png", "dozer-2.png", "dozer-3.png")[variant]
    elif "excavator" in equipment_type:
        filename = ("excavator.png", "excavator-2.png", "excavator-3.png")[variant]
    else:
        filename = (
            "skid-steer.png",
            "skid-steer-2.png",
            "skid-steer-3.png",
        )[variant]
    path = IMAGE_DIR / filename
    if not path.exists():
        raise RuntimeError(f"Missing representative image: {path}")
    return path


def equipment_type_key(row: dict[str, str]) -> str:
    normalized = value(row, "Equipment Type").strip().lower().replace("-", " ")
    return {
        "skid steer": "skid_steer",
        "compact track loader": "skid_steer",
        "dozer": "dozer",
        "excavator": "excavator",
        "tracked excavator": "excavator",
        "mini excavator": "mini_excavator",
        "telehandler": "telehandler",
        "forklift": "forklift",
        "tractor": "tractor",
        "loader": "loader",
    }.get(normalized, "other")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument(
        "--create-missing",
        action="store_true",
        help="Create a source ID only when neither its composite key nor URL exists.",
    )
    parser.add_argument(
        "--expect-published",
        action="store_true",
        help="Read-only post-publication verification; cannot be combined with --apply.",
    )
    args = parser.parse_args()
    if args.publish and not args.apply:
        raise RuntimeError("--publish requires --apply.")
    if args.expect_published and args.apply:
        raise RuntimeError("--expect-published is read-only and cannot be combined with --apply.")

    rows = read_rows(args.input)
    ids = [value(row, "Source Listing ID", "Equipment ID") for row in rows]
    if any(not item for item in ids) or len(ids) != len(set(ids)):
        raise RuntimeError("Source Listing IDs must be present and unique.")

    connection = connect()
    fields = call(
        connection,
        "fields_get",
        [],
        {"attributes": ["string", "type", "readonly"]},
    )
    photo_rights_field = field_by_label(fields, "Photo Rights Confirmed")
    photo_note_field = field_by_label(fields, "Photo Source / License Note")
    representative_field = field_by_label(fields, "Representative / Generic Image")
    verification_fields = sorted(
        f"{name}:{spec.get('string')}"
        for name, spec in fields.items()
        if "verif" in str(spec.get("string") or "").lower()
        or "confirm" in str(spec.get("string") or "").lower()
    )

    read_fields = [
        name
        for name in (
            "id",
            "company_id",
            "source_listing_id",
            "public_title",
            "public_status",
            "public_region",
            "public_price",
            "website_published",
            "seller_ask_price",
            "public_description",
            "verification_note",
            "image_1920",
            "internal_notes",
            photo_rights_field,
            photo_note_field,
            representative_field,
        )
        if name and name in fields
    ]
    live = call(
        connection,
        "search_read",
        [[("source", "=", "facebook_marketplace"), ("source_listing_id", "in", ids)]],
        {"fields": read_fields, "limit": len(ids) + 5},
    )
    by_source_id: dict[str, list[dict]] = {}
    for record in live:
        by_source_id.setdefault(str(record.get("source_listing_id") or ""), []).append(record)
    bad_matches = {
        item: len(by_source_id.get(item, []))
        for item in ids
        if len(by_source_id.get(item, [])) > 1
        or (not args.create_missing and len(by_source_id.get(item, [])) != 1)
    }
    if bad_matches:
        raise RuntimeError(f"Every source ID must match exactly one Odoo record: {bad_matches}")

    planned: list[tuple[int | None, str, dict]] = []
    for row in rows:
        item = value(row, "Source Listing ID", "Equipment ID")
        record = by_source_id.get(item, [None])[0]
        canonical_url = value(row, "Canonical Source URL", "Source URL", "Facebook URL")
        original_facebook_url = value(row, "Original Facebook Link")
        if (
            not record
            and original_facebook_url
            and "facebook_shared_url" in fields
        ):
            shared_matches = call(
                connection,
                "search_read",
                [[
                    ("source", "=", "facebook_marketplace"),
                    ("facebook_shared_url", "=", original_facebook_url.rstrip("/")),
                ]],
                {"fields": read_fields, "limit": 2},
            )
            if len(shared_matches) > 1:
                raise RuntimeError(
                    f"{item}: original Facebook link matches multiple Odoo records."
                )
            record = shared_matches[0] if shared_matches else None
        if not record and args.create_missing:
            url_matches = call(
                connection,
                "search_count",
                [[("source", "=", "facebook_marketplace"), ("source_url", "=", canonical_url)]],
            )
            if url_matches:
                raise RuntimeError(
                    f"{item}: canonical URL already exists on {url_matches} Odoo record(s)."
                )
        seller_ask = number(value(row, "Seller Ask", "Seller Ask Price", "Ask Price"))
        public_price = number(value(row, "Public Price"))
        expected_price = round(seller_ask * 1.05, 2)
        if not seller_ask or abs(public_price - expected_price) >= 0.01:
            raise RuntimeError(
                f"{item}: hardened public price {public_price:.2f} does not equal "
                f"seller ask {seller_ask:.2f} x 1.05 ({expected_price:.2f})."
            )
        region = broad_region(row)
        if not region:
            raise RuntimeError(f"{item}: no safe public region.")
        update = {
            "company_id": TARGET_COMPANY_ID,
            "public_title": value(
                row,
                "Standardized Title",
                "Public Title",
                "Equipment Name",
                "Opportunity",
            ),
            "public_region": region,
            "public_price": public_price,
            "public_description": public_description(row, region),
            "equipment_type": equipment_type_key(row),
            "verification_note": (
                "Availability, condition, and seller details are being verified. "
                "Representative image shown."
            ),
            "image_1920": base64.b64encode(image_path(row).read_bytes()).decode("ascii"),
            "public_status": "verification_in_progress",
            "website_published": bool(args.publish),
        }
        source_values = {
            "source_listing_id": item,
            "source_url": canonical_url,
            "capture_run_id": value(row, "Capture Run ID"),
            "raw_capture_text": value(row, "Raw Capture Text"),
            "seller_name_raw": value(row, "Seller Name Raw", "Seller Name"),
            "seller_phone": value(row, "Seller Phone"),
            "seller_email": value(row, "Seller Email"),
            "seller_ask_price": seller_ask,
            "seller_exact_location": value(
                row,
                "Restricted Exact Location",
                "Seller Exact Location",
                "Location",
            ),
            "ask_price": seller_ask,
            "manufacturer": value(row, "Manufacturer"),
            "model": value(row, "Model"),
            "year": int(number(value(row, "Year"))),
            "hours": number(value(row, "Hours")),
        }
        update.update(
            {
                key: item_value
                for key, item_value in source_values.items()
                if key in fields and item_value not in ("", False, 0, 0.0)
            }
        )
        if original_facebook_url and "facebook_shared_url" in fields:
            update["facebook_shared_url"] = original_facebook_url.rstrip("/")
        if "facebook_intake_status" in fields:
            update["facebook_intake_status"] = "resolved"
        if "facebook_intake_error" in fields:
            update["facebook_intake_error"] = False
        if not record:
            update.update(
                {
                    "name": item,
                    "source": "facebook_marketplace",
                    "deal_score": 50.0,
                    "grade": "verify",
                }
            )
        if photo_rights_field:
            update[photo_rights_field] = True
        if photo_note_field:
            update[photo_note_field] = ASSET_NOTE
        if representative_field:
            update[representative_field] = True
        existing_notes = str((record or {}).get("internal_notes") or "")
        if ASSET_NOTE not in existing_notes and "internal_notes" in fields:
            update["internal_notes"] = (
                existing_notes + f"<p>{html.escape(ASSET_NOTE)}</p>"
            )
        planned.append((int(record["id"]) if record else None, item, update))

    print(f"MODE={'APPLY+PUBLISH' if args.publish else 'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"TARGET_COMPANY_ID={TARGET_COMPANY_ID}")
    print(f"TARGET_COMPANY={TARGET_COMPANY_NAME}")
    print(f"ROWS={len(planned)}")
    print(f"CREATES={sum(record_id is None for record_id, _item, _update in planned)}")
    print(
        "LIVE_PHOTO_FIELDS="
        f"rights:{photo_rights_field or 'missing'},"
        f"note:{photo_note_field or 'missing'},"
        f"representative:{representative_field or 'missing'}"
    )
    print("LIVE_VERIFICATION_FIELDS=" + ",".join(verification_fields))
    if args.apply:
        for record_id, _item, update in planned:
            if record_id is None:
                call(connection, "create", [update])
            else:
                call(connection, "write", [[record_id], update])

    verify = call(
        connection,
        "search_read",
        [[("source", "=", "facebook_marketplace"), ("source_listing_id", "in", ids)]],
        {"fields": read_fields, "limit": len(ids) + 5},
    )
    if args.apply or args.expect_published:
        expected_published = args.publish or args.expect_published
        verified = 0
        for record in verify:
            company = record.get("company_id")
            company_id = company[0] if isinstance(company, (list, tuple)) and company else company
            rights_ok = not photo_rights_field or bool(record.get(photo_rights_field))
            note_ok = not photo_note_field or bool(record.get(photo_note_field))
            representative_ok = (
                not representative_field or bool(record.get(representative_field))
            )
            if (
                company_id == TARGET_COMPANY_ID
                and
                record.get("public_region")
                and number(record.get("public_price")) > 0
                and record.get("public_description")
                and record.get("image_1920")
                and rights_ok
                and note_ok
                and representative_ok
                and bool(record.get("website_published")) == expected_published
            ):
                verified += 1
        print(f"VERIFIED={verified}/{len(rows)}")
        if verified != len(rows):
            raise RuntimeError("Post-write verification did not reconcile.")
    else:
        print("WRITES=0")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, xmlrpc.client.Error) as exc:
        print(f"ERROR={exc}", file=sys.stderr)
        raise SystemExit(1)
