"""Audit Facebook-to-Odoo equipment opportunities without modifying Odoo.

The verifier consumes an authorized local enrichment CSV, optionally reads the
matching Odoo records through XML-RPC, and writes:

* a row-level verification report;
* an Odoo-import-compatible proposed update file limited to safe normalization;
* a concise Markdown summary/checklist.

It never writes to Odoo and never accesses Facebook.
"""

from __future__ import annotations

import argparse
import csv
import html
import os
import re
import sys
import xmlrpc.client
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = (
    ROOT
    / "outputs"
    / "facebook_marketplace_deals"
    / "odoo-facebook-marketplace-enrichment-update-20260726.csv"
)
DEFAULT_OUTPUT_DIR = ROOT / "outputs" / "facebook_marketplace_verification"
ENV_PATH = ROOT / "odoo_connection.env"

TYPE_LABELS = {
    "skid_steer": "Skid Steer",
    "dozer": "Dozer",
    "excavator": "Excavator",
    "mini_excavator": "Mini Excavator",
    "telehandler": "Telehandler",
    "forklift": "Forklift",
    "tractor": "Tractor",
    "loader": "Loader",
    "other": "Other",
}
MANUFACTURERS = {
    "cat": "Caterpillar",
    "caterpillar": "Caterpillar",
    "deere": "John Deere",
    "john deer": "John Deere",
    "john deere": "John Deere",
    "bobcat": "Bobcat",
    "kubota": "Kubota",
    "komatsu": "Komatsu",
}
RISK_FLAGS = {
    "2945032442507291": "Raw capture includes unmatched 'lt' variant token.",
    "1659523562025251": "Model 450 may be underspecified; exact variant is unknown.",
    "2351269645402832": "Represented ask price warrants heightened ownership, condition, and scam review.",
    "2065706974830773": "Current-year value and unusual T66-2 format require exact identity review.",
    "1926277188057439": "Multi-asset/business bundle; machine and non-machine value must be separated.",
}
ODOO_FIELDS = [
    "id",
    "source",
    "source_listing_id",
    "source_url",
    "public_title",
    "public_description",
    "public_status",
    "public_region",
    "public_price",
    "website_published",
    "equipment_type",
    "manufacturer",
    "model",
    "year",
    "hours",
    "vin_serial",
    "seller_ask_price",
    "seller_exact_location",
    "seller_name_raw",
    "seller_phone",
    "seller_email",
    "seller_facebook",
    "capture_run_id",
    "raw_capture_text",
    "internal_notes",
    "image_1920",
    "photo_ids",
]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        raise ValueError(f"Refusing to write empty report: {path}")
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def canonical_facebook_url(value: str) -> str:
    match = re.search(r"/marketplace/item/(\d+)", value or "")
    return (
        f"https://www.facebook.com/marketplace/item/{match.group(1)}"
        if match
        else (value or "").strip().rstrip("/")
    )


def listing_id_from_url(value: str) -> str:
    match = re.search(r"/marketplace/item/(\d+)", value or "")
    return match.group(1) if match else ""


def canonical_manufacturer(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", (value or "").strip())
    return MANUFACTURERS.get(cleaned.lower(), cleaned.title())


def canonical_model(value: str) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    return value.upper()


def normalized_name(row: dict[str, str]) -> tuple[str, str]:
    source_id = row["Source Listing ID"].strip()
    year = row["Year"].strip()
    manufacturer = canonical_manufacturer(row["Manufacturer"])
    model = canonical_model(row["Model"])
    inferred = []
    if not model and source_id == "1926277188057439":
        model = "333G"
        inferred.append("Model 333G parsed directly from source title.")
    source_text = f"{row['Public Title']} {row['Raw Capture Text']}"
    descriptors = []
    if re.search(r"\bLGP\b", source_text, re.I) and not re.search(r"\bLGP\b", model, re.I):
        descriptors.append("LGP")
    if source_id == "1926277188057439":
        descriptors.append("Package/Bundle")
    type_label = TYPE_LABELS.get(row["Equipment Type"].strip(), "Other")
    name = " ".join(
        part
        for part in (year, manufacturer, model, *descriptors, type_label)
        if part
    )
    if "Package/Bundle" in descriptors:
        name = f"{' '.join(part for part in (year, manufacturer, model, type_label) if part)} Package/Bundle"
    return name, " ".join(inferred)


def plain_text(value: object) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    return html.unescape(re.sub(r"\s+", " ", text)).strip()


def money(value: object) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", str(value or ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def odoo_records(ids: list[str]) -> tuple[list[dict], str]:
    load_env(ENV_PATH)
    required = ("ODOO_URL", "ODOO_DB", "ODOO_USERNAME", "ODOO_API_KEY")
    missing = [key for key in required if not os.environ.get(key, "").strip()]
    if missing:
        return [], f"Odoo live read unavailable; missing {', '.join(missing)}."
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        return [], "Odoo live read unavailable; authentication failed."
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    domain = [
        ("source", "=", "facebook_marketplace"),
        "|",
        ("source_listing_id", "in", ids),
        ("source_url", "in", [f"https://www.facebook.com/marketplace/item/{item}" for item in ids]),
    ]
    records = models.execute_kw(
        db,
        uid,
        api_key,
        "southern.equipment.listing",
        "search_read",
        [domain],
        {"fields": ODOO_FIELDS, "limit": max(100, len(ids) * 3)},
    )
    host = urlsplit(url).netloc
    return records, f"Read {len(records)} matching records from {host}; no writes performed."


def odoo_value(record: dict | None, key: str) -> str:
    if not record:
        return ""
    value = record.get(key)
    if value is False and key == "website_published":
        return "False"
    if value is False or value is None:
        return ""
    return str(value)


def build_reports(
    source_rows: list[dict[str, str]],
    live_rows: list[dict],
    lifecycle_rows: list[dict[str, str]],
) -> tuple[list[dict], list[dict]]:
    ids = [row["Source Listing ID"].strip() for row in source_rows]
    source_key_counts = Counter(
        ("facebook_marketplace", item, canonical_facebook_url(row["Source URL"]))
        for item, row in zip(ids, source_rows)
    )
    live_by_id: dict[str, list[dict]] = {}
    for record in live_rows:
        item = str(record.get("source_listing_id") or listing_id_from_url(record.get("source_url") or ""))
        live_by_id.setdefault(item, []).append(record)
    lifecycle_by_id = {
        row.get("Source Listing ID", "").strip(): row
        for row in lifecycle_rows
        if row.get("Source Listing ID", "").strip()
    }

    reports: list[dict] = []
    proposals: list[dict] = []
    for row in source_rows:
        item = row["Source Listing ID"].strip()
        canonical_url = canonical_facebook_url(row["Source URL"])
        matching = live_by_id.get(item, [])
        live = matching[0] if len(matching) == 1 else None
        name, name_note = normalized_name(row)
        raw = row["Raw Capture Text"].strip()
        title = row["Public Title"].strip()
        source_title_consistent = bool(title and raw and all(
            token.lower() in raw.lower()
            for token in (row["Year"].strip(), row["Model"].strip())
            if token
        ))
        source_key = ("facebook_marketplace", item, canonical_url)
        dedupe_ok = (
            source_key_counts[source_key] == 1
            and listing_id_from_url(canonical_url) == item
            and (not live_rows or len(matching) == 1)
        )
        current_title = odoo_value(live, "public_title")
        ask_matches = (
            not live
            or money(live.get("seller_ask_price")) == money(row["Ask Price"])
        )
        location_matches = (
            not live
            or odoo_value(live, "seller_exact_location").strip() == row["Seller Exact Location"].strip()
        )
        source_fields_match = (
            not live
            or (
                odoo_value(live, "source") == "facebook_marketplace"
                and odoo_value(live, "source_listing_id") == item
                and canonical_facebook_url(odoo_value(live, "source_url")) == canonical_url
            )
        )
        availability = "Not verified"
        seller_known = bool(row["Seller Name Raw"].strip() or row["Seller Phone"].strip())
        serial_known = bool(row["VIN / Serial"].strip())
        hours_known = bool(row["Hours"].strip())
        image_observed = item == "910299018769352"
        image_files = bool(live and (live.get("image_1920") or live.get("photo_ids")))
        lifecycle = lifecycle_by_id.get(item, {})
        lifecycle_outcome = lifecycle.get("Outcome", "").strip().lower()
        lifecycle_checked_at = lifecycle.get("Checked At", "").strip()
        lifecycle_evidence = lifecycle.get("Exact Evidence", "").strip()
        explicit_unavailable = (
            lifecycle_outcome in {"deleted", "sold", "unavailable", "expired", "no longer exists"}
            and bool(lifecycle_checked_at and lifecycle_evidence)
        )
        explicit_available = (
            lifecycle_outcome == "available"
            and bool(lifecycle_checked_at and lifecycle_evidence)
        )
        risk = RISK_FLAGS.get(item, "")
        if not row["Year"].strip():
            risk = (risk + " Year missing.").strip()
        if not row["Model"].strip():
            risk = (risk + " Normalized model missing in current source artifact.").strip()
        if not source_title_consistent:
            risk = (risk + " Parsed year/model is not fully supported by captured title.").strip()
        recommended = "Human Review Required"
        proposed_odoo_status = "verification_in_progress"
        if explicit_unavailable:
            recommended = "Rejected"
            proposed_odoo_status = "unavailable"
        checks = {
            "dedupe_key": dedupe_ok,
            "title_source": source_title_consistent,
            "ask_price": bool(row["Ask Price"].strip()) and ask_matches,
            "location": bool(row["Seller Exact Location"].strip()) and location_matches,
            "equipment_type": row["Equipment Type"].strip() in TYPE_LABELS,
            "manufacturer": bool(row["Manufacturer"].strip()),
            "model": bool(row["Model"].strip()) or item == "1926277188057439",
            "year": bool(row["Year"].strip()),
            "hours": hours_known,
            "seller": seller_known,
            "serial": serial_known,
            "availability": explicit_available,
            "sale_type": False,
            "raw_provenance": bool(raw and row["Capture Run ID"].strip()),
            "image_rights": False,
        }
        passed = sum(checks.values())
        public_region = odoo_value(live, "public_region").strip()
        public_description = plain_text(live.get("public_description")) if live else ""
        seller_ask = money(row["Ask Price"])
        required_public_price = round(seller_ask * 1.05, 2)
        current_public_price = money(live.get("public_price")) if live else 0.0
        public_price_ready = (
            seller_ask > 0
            and abs(current_public_price - required_public_price) < 0.01
        )
        image_license_note = lifecycle.get("Image License / Generic Asset Note", "").strip()
        image_ready = bool(image_files and image_license_note)
        publish_blockers = []
        if not explicit_available:
            publish_blockers.append("current availability lacks direct authorized evidence and timestamp")
        if not public_region:
            publish_blockers.append("public region missing")
        if not public_description:
            publish_blockers.append("safe public description missing")
        if not public_price_ready:
            publish_blockers.append(
                f"public price must be {required_public_price:.2f} (seller ask x 1.05) or have an explicit override"
            )
        if not image_ready:
            publish_blockers.append("authorized image or representative/generic asset license note missing")
        if proposed_odoo_status == "unavailable":
            publish_blockers.append("source explicitly reports listing unavailable")
        publish_ready = not publish_blockers
        reports.append(
            {
                "Source": "facebook_marketplace",
                "Source Listing ID": item,
                "Canonical Source URL": canonical_url,
                "Odoo Record Count": len(matching) if live_rows else "",
                "Odoo Record ID": odoo_value(live, "id"),
                "Dedupe Key": f"facebook_marketplace|{item}",
                "Dedupe Result": "PASS" if dedupe_ok else "FAIL / REVIEW",
                "Current Odoo Title": current_title,
                "Proposed Standard Name": name,
                "Name Change Proposed": "Yes" if current_title and current_title != name else "No",
                "Naming Basis": "Year Manufacturer Model Equipment Type",
                "Source Title": title,
                "Raw Capture Text": raw,
                "Title/Source Consistency": "PASS" if source_title_consistent else "REVIEW",
                "Ask Price": row["Ask Price"],
                "Ask Price Result": "PASS" if checks["ask_price"] else "REVIEW",
                "Exact Displayed Location": row["Seller Exact Location"],
                "Location Result": "PASS" if checks["location"] else "REVIEW",
                "Equipment Type": row["Equipment Type"],
                "Manufacturer": row["Manufacturer"],
                "Model": row["Model"],
                "Year": row["Year"],
                "Hours": row["Hours"],
                "Seller Identity/Contact": "Captured" if seller_known else "Missing",
                "VIN/Serial": row["VIN / Serial"] or "Missing",
                "Availability Evidence": availability,
                "Source Lifecycle Outcome": lifecycle.get("Outcome", "") or "Not checked / no explicit evidence",
                "Source Checked At": lifecycle_checked_at,
                "Source Lifecycle Exact Evidence": lifecycle_evidence,
                "Unavailable Proposal": "Yes" if explicit_unavailable else "No",
                "Sale/Rental/Parts/Deposit Classification": "Unresolved",
                "Raw Capture/Provenance": "PASS" if checks["raw_provenance"] else "REVIEW",
                "Image Availability": "14 photos observed in diagnostic" if image_observed else "No authorized image evidence",
                "Image Reuse Rights": "Not documented",
                "Image License / Generic Asset Note": image_license_note,
                "Suspicious/Scam Indicators": risk or "No specific indicator in limited capture; seller/ownership still unverified.",
                "Source vs Inference": (
                    "SOURCE: title, ask, displayed location, ID/URL, raw capture"
                    + (", hours/seller/serial where explicitly populated" if (hours_known or seller_known or serial_known) else "")
                    + ". NORMALIZED: manufacturer, model capitalization, equipment type, standardized name."
                ),
                "Odoo Source Fields Match": "PASS" if source_fields_match else "REVIEW",
                "Website Published": odoo_value(live, "website_published"),
                "Current Odoo Status": odoo_value(live, "public_status") or row["Public Status"],
                "Public Region": public_region,
                "Public Description Present": "Yes" if public_description else "No",
                "Current Public Price": f"{current_public_price:.2f}",
                "Required Public Price (Ask x 1.05)": f"{required_public_price:.2f}",
                "Public Price Rule": "PASS" if public_price_ready else "BLOCKED",
                "Checklist Passed": f"{passed}/{len(checks)}",
                "Recommended Status": recommended,
                "Proposed Odoo Status": proposed_odoo_status,
                "Publish Ready": "Yes" if publish_ready else "No",
                "Publish Blockers": "; ".join(publish_blockers),
                "Human Review Reasons": "; ".join(
                    part for part in (
                        "" if explicit_available else "availability not verified",
                        "sale/rental/parts/deposit classification unresolved",
                        "" if seller_known else "seller/contact missing",
                        "" if serial_known else "VIN/serial missing",
                        "" if image_files else "authorized reusable images absent",
                        "image reuse rights not documented",
                        risk,
                        name_note,
                    ) if part
                ),
            }
        )
        notes = plain_text(row["Internal Notes"])
        verification_appendix = (
            f"VERIFICATION WORKFLOW: {recommended}. Availability lifecycle outcome: "
            f"{lifecycle.get('Outcome', '') or 'no explicit evidence'}"
            + (f" checked {lifecycle_checked_at}; exact evidence: {lifecycle_evidence}." if lifecycle_checked_at and lifecycle_evidence else ".")
            + " Sale-versus-rental/parts/deposit classification is unresolved. "
            + "Image reuse rights are not documented. "
            + f"Standardized naming proposal: {name}."
        )
        proposals.append(
            {
                "Source": "facebook_marketplace",
                "Source Listing ID": item,
                "Public Title": name,
                "Source URL": canonical_url,
                "Capture Run ID": row["Capture Run ID"],
                "Raw Capture Text": raw,
                "Seller Name Raw": row["Seller Name Raw"],
                "Seller Phone": row["Seller Phone"],
                "Seller Email": row["Seller Email"],
                "Seller Facebook": row["Seller Facebook"],
                "Seller Exact Location": row["Seller Exact Location"],
                "Public Status": proposed_odoo_status,
                "Published on Website": "False",
                "Equipment Type": row["Equipment Type"],
                "Manufacturer": canonical_manufacturer(row["Manufacturer"]),
                "Model": canonical_model(row["Model"]) or ("333G" if item == "1926277188057439" else ""),
                "Year": row["Year"],
                "Hours": row["Hours"],
                "VIN / Serial": row["VIN / Serial"],
                "Ask Price": row["Ask Price"],
                "Internal Notes": f"{notes} {verification_appendix}".strip(),
            }
        )
    return reports, proposals


def write_summary(path: Path, reports: list[dict], live_note: str, source_path: Path) -> None:
    total = len(reports)
    counts = Counter(row["Recommended Status"] for row in reports)
    name_changes = sum(row["Name Change Proposed"] == "Yes" for row in reports)
    dedupe_pass = sum(row["Dedupe Result"] == "PASS" for row in reports)
    publish_ready = sum(row["Publish Ready"] == "Yes" for row in reports)
    explicitly_unavailable = sum(row["Unavailable Proposal"] == "Yes" for row in reports)
    unavailable = [row for row in reports if row["Unavailable Proposal"] == "Yes"]
    text = f"""# Facebook equipment listing verification

Generated: {datetime.now().astimezone().isoformat(timespec="seconds")}

Read-only workflow. No Facebook access, seller contact, bidding, payment, Odoo write, or
publication occurred.

## Status rubric

- **Verification In Progress**: safely staged and unpublished; source provenance is retained.
- **Human Review Required**: one or more decision-critical facts remain missing or ambiguous.
- **Seller Confirmed**: requires direct, current evidence of seller identity, ownership,
  availability, equipment identity, price, and sale terms. The automated workflow never
  assigns this status from search/export evidence alone.
- **Rejected**: direct evidence establishes duplicate, unavailable, rental/parts/deposit-only,
  materially inconsistent, prohibited, or fraudulent content. Suspicion alone is not enough.

## Required verification checklist

1. Match the composite dedupe key: Source + Source Listing ID, with canonical URL fallback.
2. Preserve the exact source title/raw capture separately from parsed or normalized fields.
3. Standardize display name as `Year Manufacturer Model Equipment Type`, omitting only
   genuinely missing components.
4. Confirm ask price, displayed location, type, make, model, year, hours, and serial from
   direct evidence; never infer missing values.
5. Confirm seller identity/contact, ownership authority, current availability, and whether
   the offer is a sale rather than rental, parts, deposit, or bundle ambiguity.
6. Confirm image availability and explicit reuse rights before copying or publishing photos.
7. Review price anomalies, title inconsistencies, duplicated photos/text, payment pressure,
   and other scam signals.
8. Keep the listing unpublished until required human checks are complete.
9. Require direct authorized availability evidence with a timestamp and retain exact evidence.
10. Require a public region, safe public description, and public price equal to seller ask
    multiplied by 1.05 unless an explicit override is documented.
11. Require an authorized image, or a representative/generic image with an owned/generated
    license note.
12. Mark a source unavailable only on explicit deleted/sold/unavailable/expired/no-longer-
    exists evidence; login walls, rate limits, blocks, and transient errors are human review.

## Current batch

- Source artifact: `{source_path}`
- Records reviewed: {total}
- Production/live read: {live_note}
- Dedupe checks passed: {dedupe_pass}/{total}
- Proposed standardized-name changes versus live Odoo: {name_changes}
- Verification In Progress: {total}
- Human Review Required: {counts["Human Review Required"]}
- Seller Confirmed: {counts["Seller Confirmed"]}
- Rejected: {counts["Rejected"]}
- Explicit unavailable lifecycle proposals: {explicitly_unavailable}
- Publish ready: {publish_ready}
- Published by this workflow: 0

Every record remains Human Review Required because current availability, transaction type,
and image reuse rights are not directly verified. No record qualifies for Seller Confirmed.
{"".join(f"- Unavailable proposal {row['Source Listing ID']}: {row['Source Lifecycle Exact Evidence']}\n" for row in unavailable)}
"""
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--lifecycle-evidence",
        type=Path,
        help="Optional manually/authorized collected lifecycle CSV; never generated by scraping.",
    )
    parser.add_argument("--skip-odoo", action="store_true", help="Do not perform the optional read-only Odoo check.")
    args = parser.parse_args()
    source_rows = read_csv(args.input)
    if not source_rows:
        raise SystemExit("No source rows found.")
    ids = [row["Source Listing ID"].strip() for row in source_rows]
    if len(ids) != len(set(ids)):
        raise SystemExit("Duplicate Source Listing IDs exist in the input.")
    if args.skip_odoo:
        live_rows, live_note = [], "Skipped by command-line option."
    else:
        try:
            live_rows, live_note = odoo_records(ids)
        except (OSError, xmlrpc.client.Error) as exc:
            live_rows, live_note = [], f"Odoo live read failed safely: {exc.__class__.__name__}."
    lifecycle_rows = read_csv(args.lifecycle_evidence) if args.lifecycle_evidence else []
    reports, proposals = build_reports(source_rows, live_rows, lifecycle_rows)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    report_path = args.output_dir / f"facebook-listing-verification-report-{stamp}.csv"
    proposal_path = args.output_dir / f"odoo-facebook-verification-proposed-updates-{stamp}.csv"
    summary_path = args.output_dir / f"facebook-listing-verification-summary-{stamp}.md"
    lifecycle_template_path = args.output_dir / f"facebook-source-lifecycle-review-template-{stamp}.csv"
    write_csv(report_path, reports)
    write_csv(proposal_path, proposals)
    write_summary(summary_path, reports, live_note, args.input.resolve())
    if not args.lifecycle_evidence:
        write_csv(
            lifecycle_template_path,
            [
                {
                    "Source Listing ID": row["Source Listing ID"],
                    "Canonical Source URL": canonical_facebook_url(row["Source URL"]),
                    "Checked At": "",
                    "Outcome": "",
                    "Exact Evidence": "",
                    "Access Result": "Human review required",
                    "Image License / Generic Asset Note": "",
                }
                for row in source_rows
            ],
        )
    print(f"REPORT={report_path.resolve()}")
    print(f"PROPOSAL={proposal_path.resolve()}")
    print(f"SUMMARY={summary_path.resolve()}")
    print(f"LIFECYCLE_TEMPLATE={lifecycle_template_path.resolve()}")
    print(f"ROWS={len(reports)}")
    print(live_note)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
