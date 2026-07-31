from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


OUTPUT_FIELDS = [
    "Priority",
    "Internal Reference",
    "Name",
    "Category",
    "Current Cost/Vendor Price",
    "Current Odoo Sales Price",
    "Research Query 1",
    "Research Query 2",
    "Research Query 3",
    "Evidence URL 1",
    "Evidence Price 1",
    "Evidence URL 2",
    "Evidence Price 2",
    "Evidence URL 3",
    "Evidence Price 3",
    "Proposed Retail Price",
    "Pricing Method",
    "Confidence",
    "Notes",
]


PRIORITY_PATTERNS = [
    (re.compile(r"pto|driveline|yoke|shaft|clutch", re.I), 10),
    (re.compile(r"hydraulic|pump|cylinder|hose|coupler|valve", re.I), 9),
    (re.compile(r"electrical|starter|alternator|switch|sensor", re.I), 8),
    (re.compile(r"engine|cooling|radiator|water pump|thermostat", re.I), 8),
    (re.compile(r"filter|seal|bearing|belt", re.I), 7),
    (re.compile(r"hardware|fastener|bolt|nut|washer|pin", re.I), 5),
]


def latest_proposal() -> Path:
    files = sorted(PRICING_DIR.glob("sparex_retail_price_proposals_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No sparex_retail_price_proposals_*.csv file found. Run sparex_build_retail_pricing_proposals.py first.")
    return files[0]


def priority_for(row: dict[str, str]) -> int:
    text = f"{row.get('Name', '')} {row.get('Category', '')}"
    score = 1
    for pattern, value in PRIORITY_PATTERNS:
        if pattern.search(text):
            score = max(score, value)
    current_price = row.get("Current Odoo Sales Price", "").strip()
    if current_price in {"", "0.00", "1.00"}:
        score += 2
    return min(score, 10)


def search_queries(sku: str, name: str) -> tuple[str, str, str]:
    short_name = re.sub(r"\s+-\s+Sparex\s+S\.\d+.*$", "", name, flags=re.I).strip()
    short_name = re.sub(r"\s+", " ", short_name)
    return (
        f'"{sku}" price',
        f'"Sparex {sku}" retail price',
        f'"{sku}" "{short_name[:70]}"',
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a batchable Sparex retail price research queue from the proposal CSV.")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--min-priority", type=int, default=1)
    args = parser.parse_args()

    input_path = args.input or latest_proposal()
    rows = list(csv.DictReader(input_path.open(newline="", encoding="utf-8-sig")))
    queue_rows = []
    for row in rows:
        if row.get("Proposed Retail Price", "").strip():
            continue
        priority = priority_for(row)
        if priority < args.min_priority:
            continue
        sku = row.get("Internal Reference", "").strip()
        name = row.get("Name", "").strip()
        q1, q2, q3 = search_queries(sku, name)
        queue_rows.append(
            {
                "Priority": priority,
                "Internal Reference": sku,
                "Name": name,
                "Category": row.get("Category", ""),
                "Current Cost/Vendor Price": row.get("Current Cost/Vendor Price", ""),
                "Current Odoo Sales Price": row.get("Current Odoo Sales Price", ""),
                "Research Query 1": q1,
                "Research Query 2": q2,
                "Research Query 3": q3,
                "Evidence URL 1": "",
                "Evidence Price 1": "",
                "Evidence URL 2": "",
                "Evidence Price 2": "",
                "Evidence URL 3": "",
                "Evidence Price 3": "",
                "Proposed Retail Price": "",
                "Pricing Method": "Exact SKU/OEM market research pending",
                "Confidence": "Research",
                "Notes": row.get("Notes", ""),
            }
        )

    queue_rows.sort(key=lambda row: (-int(row["Priority"]), row["Category"], row["Internal Reference"]))
    if args.limit:
        queue_rows = queue_rows[: args.limit]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = PRICING_DIR / f"sparex_retail_price_research_queue_{timestamp}.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(queue_rows)

    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print(f"Rows: {len(queue_rows)}")


if __name__ == "__main__":
    main()
