from __future__ import annotations

import csv
import statistics
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


def latest(pattern: str) -> Path:
    files = sorted(PRICING_DIR.glob(pattern), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit(f"No files match {pattern}")
    return files[0]


def read_csv(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(path.open(newline="", encoding="utf-8-sig")))


def money(value: str) -> float:
    try:
        return float(str(value or "").replace(",", "").replace("$", ""))
    except ValueError:
        return 0.0


def category_parent(category: str) -> str:
    parts = [part.strip() for part in str(category or "").split("/") if part.strip()]
    if len(parts) >= 2:
        return " / ".join(parts[:2])
    return str(category or "").strip()


def main() -> None:
    proposal_path = latest("sparex_retail_price_proposals_*.csv")
    layered_path = latest("sparex_retail_price_layered_proposals_*.csv")
    proposals = read_csv(proposal_path)
    layered = read_csv(layered_path)

    exact_category_prices: dict[str, list[float]] = defaultdict(list)
    parent_category_prices: dict[str, list[float]] = defaultdict(list)
    global_prices: list[float] = []
    for row in layered:
        price = money(row.get("Proposed Retail Price", ""))
        if price <= 0:
            continue
        category = row.get("Category", "")
        exact_category_prices[category].append(price)
        parent_category_prices[category_parent(category)].append(price)
        global_prices.append(price)

    if not global_prices:
        raise SystemExit("No layered prices found for median fallback.")

    output_by_sku = {row["Internal Reference"].strip().upper(): {field: row.get(field, "") for field in OUTPUT_FIELDS} for row in layered}
    fallback_count = 0
    exact_fallback_count = 0
    parent_fallback_count = 0
    global_fallback_count = 0
    for row in proposals:
        sku = row.get("Internal Reference", "").strip().upper()
        if not sku or sku in output_by_sku:
            continue
        category = row.get("Category", "")
        if exact_category_prices.get(category):
            fallback_price = statistics.median(exact_category_prices[category])
            fallback_source = f"exact category median for {category}"
            exact_fallback_count += 1
        elif parent_category_prices.get(category_parent(category)):
            fallback_price = statistics.median(parent_category_prices[category_parent(category)])
            fallback_source = f"parent category median for {category_parent(category)}"
            parent_fallback_count += 1
        else:
            fallback_price = statistics.median(global_prices)
            fallback_source = "global Sparex evidence median"
            global_fallback_count += 1

        output_by_sku[sku] = {
            "Internal Reference": sku,
            "Name": row.get("Name", ""),
            "Category": category,
            "Current Cost/Vendor Price": row.get("Current Cost/Vendor Price", ""),
            "Current Odoo Sales Price": row.get("Current Odoo Sales Price", ""),
            "Proposed Retail Price": f"{fallback_price:.2f}",
            "Pricing Method": "Category evidence median fallback",
            "Confidence": "Review - Category Median",
            "Evidence URLs": "",
            "Notes": (
                f"No exact SKU price evidence found yet. Proposed review price uses {fallback_source} "
                "from the already researched Sparex evidence set. Do not import this row until reviewed or replaced by exact SKU/supplier evidence."
            ),
        }
        fallback_count += 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = PRICING_DIR / f"sparex_retail_price_complete_review_proposals_{timestamp}.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(output_by_sku.values(), key=lambda item: item["Internal Reference"]))

    summary_path = PRICING_DIR / f"sparex_retail_price_complete_review_summary_{timestamp}.csv"
    with summary_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=["Metric", "Value"])
        writer.writeheader()
        writer.writerow({"Metric": "Proposal rows", "Value": len(proposals)})
        writer.writerow({"Metric": "Layered evidence prices carried forward", "Value": len(layered)})
        writer.writerow({"Metric": "Category median fallback rows", "Value": fallback_count})
        writer.writerow({"Metric": "Exact category median fallback rows", "Value": exact_fallback_count})
        writer.writerow({"Metric": "Parent category median fallback rows", "Value": parent_fallback_count})
        writer.writerow({"Metric": "Global median fallback rows", "Value": global_fallback_count})
        writer.writerow({"Metric": "Complete review proposal rows", "Value": len(output_by_sku)})

    print(f"Proposal: {proposal_path}")
    print(f"Layered evidence: {layered_path}")
    print(f"Output: {output_path}")
    print(f"Summary: {summary_path}")
    print(f"Proposal rows: {len(proposals)}")
    print(f"Layered evidence prices carried forward: {len(layered)}")
    print(f"Category median fallback rows: {fallback_count}")
    print(f"Complete review proposal rows: {len(output_by_sku)}")


if __name__ == "__main__":
    main()
