"""Normalize user-supplied pasted auction results into comp-analysis CSV rows."""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
from datetime import datetime
from pathlib import Path


TITLE_RE = re.compile(
    r"^(?:(?P<year>19\d{2}|20\d{2})\s+)?"
    r"(?P<year_unverified>\(unverified\)\s+)?"
    r"(?P<body>.+?)\s+"
    r"(?P<equipment_type>"
    r"Mini Excavator|Tracked Excavator|Skid Steer Loader|"
    r"Compact Track Loader|Crawler Dozer"
    r")"
    r"(?:\s+\((?P<condition>[^)]+)\))?$",
    re.I,
)
LOT_RE = re.compile(r"^Lot\s+(.+)$", re.I)
METER_RE = re.compile(r"^([\d,]+)\s+(hr|mi)$", re.I)
PRICE_RE = re.compile(r"^\$([\d,]+)$")
LOCATION_RE = re.compile(r"^[A-Za-z][A-Za-z .'-]+,\s*[A-Z]{2}$")
MANUFACTURERS = (
    ("John Deere", "John Deere"),
    ("Link-Belt", "Link-Belt"),
    ("Caterpillar", "Caterpillar"),
    ("Takeuchi", "Takeuchi"),
    ("Kubota", "Kubota"),
    ("Yanmar", "Yanmar"),
    ("Bobcat", "Bobcat"),
    ("Hitachi", "Hitachi"),
    ("Komatsu", "Komatsu"),
    ("Kobelco", "Kobelco"),
    ("Hyundai", "Hyundai"),
    ("Liebherr", "Liebherr"),
    ("Doosan", "Doosan"),
    ("Volvo", "Volvo"),
    ("SANY", "SANY"),
    ("Case", "Case"),
    ("JCB", "JCB"),
    ("Deere", "John Deere"),
    ("Cat", "Caterpillar"),
)


def clean_lines(raw: str) -> list[str]:
    return [
        line.strip()
        for line in raw.replace("\ufeff", "").splitlines()
        if line.strip() and line.strip() != "."
    ]


def identity(title: str) -> tuple[int | None, str, str, str, str]:
    match = TITLE_RE.match(title)
    if not match:
        raise ValueError(f"Unrecognized title: {title}")
    year = (
        int(match.group("year"))
        if match.group("year") and not match.group("year_unverified")
        else None
    )
    body = match.group("body").strip()
    equipment_type = match.group("equipment_type").lower()
    categories = {
        "mini excavator": "Mini Excavator",
        "tracked excavator": "Tracked Excavator",
        "skid steer loader": "Skid Steer Loader",
        "compact track loader": "Compact Track Loader",
        "crawler dozer": "Crawler Dozer",
    }
    category = categories[equipment_type]
    condition_parts = []
    if match.group("year_unverified"):
        condition_parts.append("Year unverified")
    if match.group("condition"):
        condition_parts.append(match.group("condition").strip())
    condition = "; ".join(condition_parts)
    for prefix, canonical in MANUFACTURERS:
        if body.lower().startswith(prefix.lower() + " "):
            model = body[len(prefix) :].strip()
            if category == "Skid Steer Loader":
                model = re.sub(
                    r"\s+(?:Two-Speed|High Flow)(?:\s+.*)?$",
                    "",
                    model,
                    flags=re.I,
                )
            elif category == "Crawler Dozer":
                model = re.sub(
                    r"\s+(?:LGP|WLT|XL|XW)$",
                    "",
                    model,
                    flags=re.I,
                )
            return year, canonical, model, category, condition
    raise ValueError(f"Unrecognized manufacturer: {title}")


def parse_file(
    path: Path,
    source_name: str = "Ritchie Bros.",
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    lines = clean_lines(path.read_text(encoding="utf-8-sig"))
    rows: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    index = 0
    while index < len(lines):
        title = lines[index]
        if not TITLE_RE.match(title):
            rejected.append({"Source File": str(path), "Line": str(index + 1), "Text": title, "Reason": "Expected equipment title"})
            index += 1
            continue
        start = index
        try:
            lot_match = LOT_RE.match(lines[index + 1])
            if not lot_match:
                raise ValueError("Missing lot number")
            lot = lot_match.group(1).strip()
            repeated_title = lines[index + 2]
            if " ".join(repeated_title.lower().split()) != " ".join(
                title.lower().split()
            ):
                raise ValueError("Repeated title does not match")
            sold_index = next(
                cursor
                for cursor in range(index + 3, min(index + 40, len(lines)))
                if lines[cursor].lower() == "sold"
            )
            location_candidates = [
                line for line in lines[index + 3 : sold_index]
                if LOCATION_RE.match(line)
            ]
            if not location_candidates:
                raise ValueError("Missing recognizable City, ST location")
            location = location_candidates[-1]
            hours = ""
            meter_note = ""
            meter_matches = [
                METER_RE.match(line)
                for line in lines[index + 3 : sold_index]
                if METER_RE.match(line)
            ]
            meter_match = meter_matches[-1] if meter_matches else None
            if meter_match:
                if meter_match.group(2).lower() == "hr":
                    hours = meter_match.group(1).replace(",", "")
                else:
                    meter_note = f"Source displayed {meter_match.group(1)} mi; not treated as hours."
            price_match = PRICE_RE.match(lines[sold_index + 1])
            if not price_match:
                raise ValueError("Missing sold price")
            if lines[sold_index + 2].upper() != "USD":
                raise ValueError("Currency is not USD")
            if not lines[sold_index + 3].lower().startswith("closed:"):
                raise ValueError("Missing close date")
            closed_raw = lines[sold_index + 3].split(":", 1)[1].strip()
            sale_date = datetime.strptime(closed_raw, "%b %d, %Y").date().isoformat()
            year, manufacturer, model, category, condition = identity(title)
            if "inoperable" in condition.lower():
                raise ValueError("Inoperable/salvage result excluded from valuation comps")
            price = price_match.group(1).replace(",", "")
            raw_record = "\n".join(lines[start : sold_index + 4])
            record_key = "|".join((title.lower(), lot.lower(), location.lower(), sale_date, price))
            record_id = hashlib.sha256(record_key.encode("utf-8")).hexdigest()[:20]
            rows.append(
                {
                    "source_name": source_name,
                    "source_record_id": record_id,
                    "result_status": "sold",
                    "price_basis": "reported_sold_price",
                    "total_price": price,
                    "currency": "USD",
                    "category": category,
                    "make": manufacturer,
                    "model": model,
                    "year": str(year) if year else "",
                    "hours": hours,
                    "sale_date": sale_date,
                    "location": location,
                    "lot_number": lot,
                    "condition_note": condition,
                    "meter_note": meter_note,
                    "canonical_url": "",
                    "capture_provenance": f"User-provided pasted auction results: {path.name}",
                    "raw_text": raw_record,
                }
            )
            index = sold_index + 4
        except (IndexError, StopIteration, ValueError) as exc:
            rejected.append({"Source File": str(path), "Line": str(start + 1), "Text": title, "Reason": str(exc)})
            index += 1
    return rows, rejected


def write_csv(path: Path, rows: list[dict[str, str]], headers: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--rejected-output", type=Path)
    parser.add_argument(
        "--source-name",
        default="Ritchie Bros.",
        help="Actual auction or data provider recorded on every normalized comp.",
    )
    args = parser.parse_args()
    parsed: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    for path in args.inputs:
        new_rows, new_rejected = parse_file(path, source_name=args.source_name)
        parsed.extend(new_rows)
        rejected.extend(new_rejected)
    unique: dict[str, dict[str, str]] = {}
    duplicate_count = 0
    for row in parsed:
        key = row["source_record_id"]
        if key in unique:
            duplicate_count += 1
        else:
            unique[key] = row
    rows = list(unique.values())
    headers = list(rows[0]) if rows else [
        "source_name", "source_record_id", "result_status", "price_basis",
        "total_price", "currency", "category", "make", "model", "year",
        "hours", "sale_date", "location", "lot_number", "condition_note",
        "meter_note", "canonical_url", "capture_provenance", "raw_text",
    ]
    write_csv(args.output, rows, headers)
    rejected_output = args.rejected_output or args.output.with_name(args.output.stem + "-rejected.csv")
    write_csv(rejected_output, rejected, ["Source File", "Line", "Text", "Reason"])
    print(f"FOUND={len(parsed)}")
    print(f"UNIQUE={len(rows)}")
    print(f"DUPLICATES={duplicate_count}")
    print(f"REJECTED={len(rejected)}")
    print(f"OUTPUT={args.output.resolve()}")
    print(f"REJECTED_OUTPUT={rejected_output.resolve()}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
