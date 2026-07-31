from __future__ import annotations

import csv
from collections import Counter
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


def latest(pattern: str) -> Path:
    files = sorted(PRICING_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"No files match {pattern}")
    return files[0]


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))


def add_layer(output_by_sku: dict[str, dict[str, str]], path: Path, layer_name: str) -> tuple[int, int]:
    added = 0
    skipped = 0
    for row in read_csv(path):
        sku = row.get("Internal Reference", "").strip().upper()
        if not sku or not row.get("Proposed Retail Price", "").strip():
            continue
        if sku in output_by_sku:
            skipped += 1
            continue
        row = {field: row.get(field, "") for field in OUTPUT_FIELDS}
        row["Notes"] = f"Layer: {layer_name}. {row.get('Notes', '')}".strip()
        output_by_sku[sku] = row
        added += 1
    return added, skipped


def main() -> None:
    proposal_path = latest("sparex_retail_price_proposals_*.csv")
    proposal_count = len(read_csv(proposal_path))
    layers = [
        ("US dealers exact SKU", latest("sparex_retail_price_researched_combined_us_dealers_*.csv")),
        ("Farming Parts GBP international reference", latest("sparex_retail_price_researched_farming_parts*_international_reference_*.csv")),
        ("Massey Tractor Parts EUR international reference", latest("sparex_retail_price_researched_massey_tractor_parts_international_reference_*.csv")),
    ]

    output_by_sku: dict[str, dict[str, str]] = {}
    layer_stats = []
    for layer_name, path in layers:
        added, skipped = add_layer(output_by_sku, path, layer_name)
        layer_stats.append((layer_name, path, added, skipped))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = PRICING_DIR / f"sparex_retail_price_layered_proposals_{timestamp}.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(output_by_sku.values(), key=lambda row: row["Internal Reference"]))

    confidence_counts = Counter(row.get("Confidence", "") for row in output_by_sku.values())
    summary_path = PRICING_DIR / f"sparex_retail_price_layered_summary_{timestamp}.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["Metric", "Value"])
        writer.writeheader()
        writer.writerow({"Metric": "Proposal rows", "Value": proposal_count})
        writer.writerow({"Metric": "Layered proposed prices", "Value": len(output_by_sku)})
        writer.writerow({"Metric": "Remaining without proposed price", "Value": proposal_count - len(output_by_sku)})
        for layer_name, path, added, skipped in layer_stats:
            writer.writerow({"Metric": f"{layer_name} added", "Value": added})
            writer.writerow({"Metric": f"{layer_name} skipped already priced", "Value": skipped})
            writer.writerow({"Metric": f"{layer_name} source", "Value": str(path)})
        for confidence, count in sorted(confidence_counts.items()):
            writer.writerow({"Metric": f"Confidence: {confidence}", "Value": count})

    print(f"Output: {output_path}")
    print(f"Summary: {summary_path}")
    print(f"Proposal rows: {proposal_count}")
    print(f"Layered proposed prices: {len(output_by_sku)}")
    print(f"Remaining without proposed price: {proposal_count - len(output_by_sku)}")
    for layer_name, path, added, skipped in layer_stats:
        print(f"{layer_name}: added={added} skipped={skipped} source={path}")
    for confidence, count in sorted(confidence_counts.items()):
        print(f"Confidence {confidence}: {count}")


if __name__ == "__main__":
    main()
