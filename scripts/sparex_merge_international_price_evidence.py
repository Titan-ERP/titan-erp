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


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a low-confidence USD retail reference from international exact SKU price evidence.")
    parser.add_argument("--proposal", type=Path, default=None)
    parser.add_argument("--evidence", type=Path, default=None)
    parser.add_argument("--source-name", default="Farming Parts")
    parser.add_argument("--currency", default="GBP")
    parser.add_argument("--fx-to-usd", type=float, default=1.3321)
    parser.add_argument("--landed-buffer", type=float, default=1.00, help="Optional multiplier after FX, e.g. 1.10 for 10 percent buffer.")
    parser.add_argument("--exclude-us-priced", action="store_true")
    args = parser.parse_args()

    proposal_path = args.proposal or latest("sparex_retail_price_proposals_*.csv")
    evidence_path = args.evidence or latest("farming_parts_sparex_price_evidence_*.csv")
    us_price_path = latest("sparex_retail_price_researched_combined_us_dealers_*.csv") if args.exclude_us_priced else None

    proposals = read_csv(proposal_path)
    proposal_by_sku = {row.get("Internal Reference", "").strip().upper(): row for row in proposals}
    us_priced = set()
    if us_price_path:
        us_priced = {row.get("Internal Reference", "").strip().upper() for row in read_csv(us_price_path)}

    evidence_by_sku: dict[str, dict[str, str]] = {}
    for row in read_csv(evidence_path):
        sku = row.get("Internal Reference", "").strip().upper()
        price = money(row.get("Evidence Price", ""))
        if not sku or price <= 0 or sku in us_priced:
            continue
        current = evidence_by_sku.get(sku)
        if current is None or price < money(current.get("Evidence Price", "")):
            evidence_by_sku[sku] = row

    output_rows: list[dict[str, str]] = []
    for sku, evidence in evidence_by_sku.items():
        proposal = proposal_by_sku.get(sku)
        if not proposal:
            continue
        local_price = money(evidence.get("Evidence Price", ""))
        usd_reference = local_price * args.fx_to_usd * args.landed_buffer
        proposed = round_retail(usd_reference)
        output_rows.append(
            {
                "Internal Reference": sku,
                "Name": proposal.get("Name", ""),
                "Category": proposal.get("Category", ""),
                "Current Cost/Vendor Price": proposal.get("Current Cost/Vendor Price", ""),
                "Current Odoo Sales Price": proposal.get("Current Odoo Sales Price", ""),
                "Proposed Retail Price": f"{proposed:.2f}",
                "Pricing Method": "International exact SKU retail reference converted to USD",
                "Confidence": "Low - International Reference",
                "Evidence URLs": evidence.get("Evidence URL", ""),
                "Notes": (
                    f"{args.source_name} lists exact SKU at {args.currency} {local_price:.2f}. "
                    f"Converted using {args.currency}/USD {args.fx_to_usd:.4f} and landed buffer {args.landed_buffer:.2f}. "
                    "Use only as review guidance until US retail, supplier feed, or approved conversion policy is confirmed."
                ),
            }
        )

    slug = re.sub(r"[^a-z0-9]+", "_", args.source_name.lower()).strip("_")
    output_path = PRICING_DIR / f"sparex_retail_price_researched_{slug}_international_reference_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(sorted(output_rows, key=lambda row: row["Internal Reference"]))

    print(f"Proposal: {proposal_path}")
    print(f"Evidence: {evidence_path}")
    if us_price_path:
        print(f"Excluded US-priced file: {us_price_path}")
    print(f"Output: {output_path}")
    print(f"Evidence rows: {len(evidence_by_sku)}")
    print(f"Proposal matches: {len(output_rows)}")
    print(f"FX to USD: {args.fx_to_usd}")
    print(f"Landed buffer: {args.landed_buffer}")


if __name__ == "__main__":
    main()
