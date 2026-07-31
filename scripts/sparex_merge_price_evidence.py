from __future__ import annotations

import argparse
import csv
import math
import re
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


SERVICE_PATTERN = re.compile(r"\b(membership|subscription|service plan|labor|labour)\b", re.I)


def latest(pattern: str) -> Path:
    files = sorted(PRICING_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"No files match {pattern}")
    return files[0]


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


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Merge exact SKU price evidence into a local researched Sparex pricing CSV.")
    parser.add_argument("--proposal", type=Path, default=None)
    parser.add_argument("--evidence", type=Path, default=None)
    parser.add_argument("--source-name", default="")
    parser.add_argument("--keep-evidence-price", action="store_true", help="Use exact evidence price instead of Southern rounding.")
    args = parser.parse_args()

    proposal_path = args.proposal or latest("sparex_retail_price_proposals_*.csv")
    evidence_path = args.evidence or latest("*_sparex_price_evidence_*.csv")
    proposals = read_csv(proposal_path)
    evidence_rows = read_csv(evidence_path)

    best_evidence: dict[str, dict[str, str]] = {}
    for row in evidence_rows:
        sku = row.get("Internal Reference", "").strip().upper()
        price = money(row.get("Evidence Price", ""))
        if not sku or price <= 0:
            continue
        current = best_evidence.get(sku)
        if current is None or price < money(current.get("Evidence Price", "")):
            best_evidence[sku] = row

    output_rows: list[dict[str, str]] = []
    for row in proposals:
        sku = row.get("Internal Reference", "").strip().upper()
        evidence = best_evidence.get(sku)
        if not evidence:
            continue
        name = row.get("Name", "")
        category = row.get("Category", "")
        evidence_price = money(evidence.get("Evidence Price", ""))
        proposed = evidence_price if args.keep_evidence_price else round_retail(evidence_price)
        source = args.source_name or evidence.get("Evidence Source", "Public price evidence")
        if SERVICE_PATTERN.search(name) or SERVICE_PATTERN.search(category):
            output_rows.append(
                {
                    "Internal Reference": sku,
                    "Name": name,
                    "Category": category,
                    "Current Cost/Vendor Price": row.get("Current Cost/Vendor Price", ""),
                    "Current Odoo Sales Price": row.get("Current Odoo Sales Price", ""),
                    "Proposed Retail Price": "",
                    "Pricing Method": "Needs Review - service item",
                    "Confidence": "Review",
                    "Evidence URLs": evidence.get("Evidence URL", ""),
                    "Notes": f"{source} found exact SKU price {evidence_price:.2f}, but item looks like a service and should not be priced as stock good.",
                }
            )
            continue
        output_rows.append(
            {
                "Internal Reference": sku,
                "Name": name,
                "Category": category,
                "Current Cost/Vendor Price": row.get("Current Cost/Vendor Price", ""),
                "Current Odoo Sales Price": row.get("Current Odoo Sales Price", ""),
                "Proposed Retail Price": f"{proposed:.2f}",
                "Pricing Method": "Exact public US Sparex SKU match",
                "Confidence": "High",
                "Evidence URLs": evidence.get("Evidence URL", ""),
                "Notes": (
                    f"{source} lists exact SKU at ${evidence_price:.2f}. "
                    f"Evidence name: {evidence.get('Evidence Name', '').strip()}. "
                    "Proposed price uses Southern retail rounding."
                ),
            }
        )

    source_slug = re.sub(r"[^a-z0-9]+", "_", (args.source_name or evidence_path.stem).lower()).strip("_")[:48]
    output_path = PRICING_DIR / f"sparex_retail_price_researched_{source_slug}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(output_rows, key=lambda item: item["Internal Reference"]))

    print(f"Proposal: {proposal_path}")
    print(f"Evidence: {evidence_path}")
    print(f"Output: {output_path}")
    print(f"Evidence rows: {len(evidence_rows)}")
    print(f"Proposal matches: {len(output_rows)}")


if __name__ == "__main__":
    main()
