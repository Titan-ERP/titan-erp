from __future__ import annotations

import csv
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


OUTPUT_FIELDS = [
    "Internal Reference",
    "Name",
    "Category",
    "Current Cost/Vendor Price",
    "Current Odoo Sales Price",
    "Proposed Retail Price",
    "Pricing Method",
    "Confidence",
    "Evidence URLs",
    "Notes",
]


SUMMARY_FIELDS = [
    "Metric",
    "Value",
]


def latest(pattern: str) -> Path:
    files = sorted(PRICING_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"No files match {pattern}")
    return files[0]


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))


def money(value: str) -> float:
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def round_retail(value: float) -> float:
    if value <= 0:
        return 0.0
    if value < 10:
        return round(math.ceil(value * 2) / 2 - 0.01, 2)
    if value < 100:
        return round(math.ceil(value) - 0.01, 2)
    if value < 500:
        return round(math.ceil(value / 5) * 5 - 0.01, 2)
    return round(math.ceil(value / 10) * 10 - 0.01, 2)


def evidence_source_priority(source: str) -> int:
    source = source.lower()
    if "lindstrom" in source:
        return 1
    if "lowe" in source:
        return 2
    return 9


def main() -> None:
    proposal_path = latest("sparex_retail_price_proposals_*.csv")
    proposals = read_csv(proposal_path)
    proposal_by_sku = {row["Internal Reference"].strip().upper(): row for row in proposals}

    evidence_by_sku: dict[str, list[dict[str, str]]] = defaultdict(list)
    evidence_files = [
        *PRICING_DIR.glob("lowe_young_sparex_price_evidence_*.csv"),
        *PRICING_DIR.glob("lindstrom_sparex_price_evidence_*.csv"),
    ]
    # Use latest full evidence files; tiny smoke-test files are harmless but can add stale duplicates.
    filtered_files = []
    for family in ("lowe_young", "lindstrom"):
        family_files = sorted([path for path in evidence_files if path.name.startswith(family)], key=lambda path: path.stat().st_mtime, reverse=True)
        if family_files:
            filtered_files.append(family_files[0])

    raw_evidence_rows = 0
    for path in filtered_files:
        rows = read_csv(path)
        raw_evidence_rows += len(rows)
        for row in rows:
            sku = row.get("Internal Reference", "").strip().upper()
            if sku and money(row.get("Evidence Price", "")) > 0:
                evidence_by_sku[sku].append(row)

    output_rows: list[dict[str, str]] = []
    overlap_count = 0
    for sku, evidence_rows in evidence_by_sku.items():
        proposal = proposal_by_sku.get(sku)
        if not proposal:
            continue
        if len({row.get("Evidence Source", "") for row in evidence_rows}) > 1:
            overlap_count += 1
        evidence_rows.sort(key=lambda row: (evidence_source_priority(row.get("Evidence Source", "")), money(row.get("Evidence Price", ""))))
        chosen = evidence_rows[0]
        price = money(chosen.get("Evidence Price", ""))
        proposed = round_retail(price)
        urls = " | ".join(dict.fromkeys(row.get("Evidence URL", "") for row in evidence_rows if row.get("Evidence URL", "")))
        sources = ", ".join(sorted(set(row.get("Evidence Source", "") for row in evidence_rows if row.get("Evidence Source", ""))))
        confidence = "High" if len(evidence_rows) == 1 else "High - Multi Source"
        output_rows.append(
            {
                "Internal Reference": sku,
                "Name": proposal.get("Name", ""),
                "Category": proposal.get("Category", ""),
                "Current Cost/Vendor Price": proposal.get("Current Cost/Vendor Price", ""),
                "Current Odoo Sales Price": proposal.get("Current Odoo Sales Price", ""),
                "Proposed Retail Price": f"{proposed:.2f}",
                "Pricing Method": "Exact public US Sparex SKU match",
                "Confidence": confidence,
                "Evidence URLs": urls,
                "Notes": (
                    f"Sources: {sources}. Chosen evidence: {chosen.get('Evidence Source', '')} at ${price:.2f}. "
                    f"Evidence name: {chosen.get('Evidence Name', '').strip()}. Proposed price uses Southern retail rounding."
                ),
            }
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = PRICING_DIR / f"sparex_retail_price_researched_combined_us_dealers_{timestamp}.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(output_rows, key=lambda row: row["Internal Reference"]))

    summary_path = PRICING_DIR / f"sparex_retail_pricing_coverage_summary_{timestamp}.csv"
    summary_rows = [
        {"Metric": "Proposal rows", "Value": len(proposals)},
        {"Metric": "Evidence files used", "Value": len(filtered_files)},
        {"Metric": "Raw evidence rows", "Value": raw_evidence_rows},
        {"Metric": "Unique evidence SKUs", "Value": len(evidence_by_sku)},
        {"Metric": "Matched proposal SKUs with proposed prices", "Value": len(output_rows)},
        {"Metric": "Matched SKUs with more than one dealer source", "Value": overlap_count},
        {"Metric": "Remaining proposal SKUs without US dealer evidence", "Value": len(proposals) - len(output_rows)},
    ]
    with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    print(f"Proposal: {proposal_path}")
    print("Evidence files:")
    for path in filtered_files:
        print(f" - {path}")
    print(f"Output: {output_path}")
    print(f"Summary: {summary_path}")
    for row in summary_rows:
        print(f"{row['Metric']}: {row['Value']}")


if __name__ == "__main__":
    main()
