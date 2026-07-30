from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


def latest(pattern: str) -> Path:
    files = sorted(PRICING_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"No files match {pattern}")
    return files[0]


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a local Odoo price import candidate CSV. Does not import anything.")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--confidence", action="append", default=["High", "High - Multi Source"])
    args = parser.parse_args()

    input_path = args.input or latest("sparex_retail_price_complete_review_proposals_*.csv")
    rows = list(csv.DictReader(input_path.open(newline="", encoding="utf-8-sig")))
    allowed = set(args.confidence)
    output_rows = [
        {
            "Internal Reference": row.get("Internal Reference", ""),
            "Sales Price": row.get("Proposed Retail Price", ""),
            "Name": row.get("Name", ""),
            "Pricing Confidence": row.get("Confidence", ""),
            "Pricing Method": row.get("Pricing Method", ""),
            "Evidence URLs": row.get("Evidence URLs", ""),
            "Pricing Notes": row.get("Notes", ""),
        }
        for row in rows
        if row.get("Confidence", "") in allowed and row.get("Proposed Retail Price", "").strip()
    ]

    output_path = PRICING_DIR / f"odoo_sparex_price_import_candidate_high_confidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "Internal Reference",
                "Sales Price",
                "Name",
                "Pricing Confidence",
                "Pricing Method",
                "Evidence URLs",
                "Pricing Notes",
            ],
        )
        writer.writeheader()
        writer.writerows(output_rows)

    print(f"Input: {input_path}")
    print(f"Output: {output_path}")
    print(f"Rows: {len(output_rows)}")


if __name__ == "__main__":
    main()
