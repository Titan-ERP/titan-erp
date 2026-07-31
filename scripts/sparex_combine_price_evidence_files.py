from __future__ import annotations

import argparse
import csv
import re
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


FIELDNAMES = [
    "Internal Reference",
    "Evidence Price",
    "Evidence Currency",
    "Evidence URL",
    "Evidence Source",
    "Evidence Name",
    "Evidence Category",
    "Harvested At",
    "Notes",
]


def money(value: str) -> float:
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Combine and dedupe price evidence CSV files by SKU, preferring the lowest positive evidence price.")
    parser.add_argument("--pattern", required=True)
    parser.add_argument("--output-prefix", required=True)
    args = parser.parse_args()

    files = sorted(PRICING_DIR.glob(args.pattern), key=lambda path: path.stat().st_mtime)
    if not files:
        raise SystemExit(f"No files match {args.pattern}")

    by_sku: dict[str, dict[str, str]] = {}
    raw_rows = 0
    for path in files:
        for row in csv.DictReader(path.open(newline="", encoding="utf-8-sig")):
            raw_rows += 1
            sku = row.get("Internal Reference", "").strip().upper()
            if not sku:
                continue
            price = money(row.get("Evidence Price", ""))
            if price <= 0:
                continue
            row = {field: row.get(field, "") for field in FIELDNAMES}
            current = by_sku.get(sku)
            if current is None or price < money(current.get("Evidence Price", "")):
                by_sku[sku] = row

    output = PRICING_DIR / f"{args.output_prefix}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(sorted(by_sku.values(), key=lambda row: row["Internal Reference"]))

    print("Files:")
    for path in files:
        print(f" - {path}")
    print(f"Output: {output}")
    print(f"Raw rows: {raw_rows}")
    print(f"Unique rows: {len(by_sku)}")


if __name__ == "__main__":
    main()
