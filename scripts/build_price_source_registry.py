from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"
DEFAULT_REGISTRY = PRICING_DIR / "retail_price_source_registry.csv"


FIELDNAMES = [
    "Internal Reference",
    "Source",
    "Price URL",
    "Search URL",
    "Currency",
    "Last Observed Price",
    "Last Checked",
    "Confidence",
    "Title",
    "Active",
    "Notes",
]


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a persistent SKU-to-retail-price-source registry from research CSVs.")
    parser.add_argument("research_csv", type=Path)
    parser.add_argument("--output", type=Path, default=DEFAULT_REGISTRY)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[tuple[str, str, str], dict[str, str]] = {}
    if args.output.exists():
        for row in read_rows(args.output):
            key = (row.get("Internal Reference", ""), row.get("Source", ""), row.get("Price URL", ""))
            if all(key):
                existing[key] = row

    checked_at = datetime.now().isoformat(timespec="seconds")
    for row in read_rows(args.research_csv):
        sku = (row.get("Internal Reference") or "").strip()
        source = (row.get("Source") or "").strip()
        price_url = (row.get("Source URL") or "").strip()
        if not sku or not source or not price_url:
            continue
        key = (sku, source, price_url)
        existing[key] = {
            "Internal Reference": sku,
            "Source": source,
            "Price URL": price_url,
            "Search URL": (row.get("Source Search URL") or "").strip(),
            "Currency": (row.get("Currency") or "").strip(),
            "Last Observed Price": (row.get("Observed Retail Price") or "").strip(),
            "Last Checked": checked_at,
            "Confidence": (row.get("Confidence") or "").strip(),
            "Title": (row.get("Title") or "").strip(),
            "Active": existing.get(key, {}).get("Active", "Yes") or "Yes",
            "Notes": (row.get("Notes") or "").strip(),
        }

    with args.output.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(sorted(existing.values(), key=lambda item: (item["Internal Reference"], item["Source"], item["Price URL"])))

    print(f"Wrote {args.output}")
    print(f"Registry rows: {len(existing)}")


if __name__ == "__main__":
    main()
