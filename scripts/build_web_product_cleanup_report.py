from __future__ import annotations

import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "odoo_imports/product_master/review_reports/odoo_product_live_inefficiency_audit.csv"
OUT = ROOT / "odoo_imports/product_master/review_reports/web_product_cleanup_opportunities.csv"


WEB_FINDINGS = {
    "594347 15/40 XTREME ENGINE OIL": {
        "proposed_name": "Engine Oil 15W-40 - Xtreme 594347",
        "family": "Lubricants",
        "category": "Parts / Lubricants",
        "manufacturer": "Xtreme",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://qa.xtremeoil.com/allproducts/xtreme-universal-engine-oils/",
        "notes": "Xtreme product page lists SKU 594347 as 4/1 Gal HD Universal SAE 15W-40 CI-4+ engine oil.",
    },
    "594507 HYDRAULIC OIL 1 GAL.": {
        "proposed_name": "Tractor Hydraulic Fluid J20C - Xtreme 594507 - 1 gal",
        "family": "Lubricants",
        "category": "Parts / Lubricants",
        "manufacturer": "Xtreme",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://xtremeoil.com/allproducts/xtreme-premium-hd-tractor-hydraulic-fluid/",
        "notes": "Xtreme product page lists SKU 594507 as 4/1 Gal Premium HD Tractor Hydraulic Fluid J20C.",
    },
    "600-185-3100": {
        "proposed_name": "Engine Air Filter Kit - Komatsu 600-185-3100",
        "family": "Air Filters",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "Komatsu",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.finditparts.com/products/11599031/komatsu-600-185-3100",
        "notes": "FinditParts lists Komatsu 600-185-3100 as KOMATSU ORIGINAL OEM, FILTER, AIR, KIT with cross references.",
    },
    "3390": {
        "proposed_name": "Fuel Filter - NAPA Gold 3390",
        "family": "Fuel Filters",
        "category": "Parts / Filters / Fuel Filters",
        "manufacturer": "NAPA",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.napaonline.com/en/p/FIL3390",
        "notes": "NAPA lists FIL 3390 as a NAPA Gold spin-on diesel fuel filter for equipment/engine applications.",
    },
    "3481": {
        "proposed_name": "Fuel Filter - NAPA Gold 3481",
        "family": "Fuel Filters",
        "category": "Parts / Filters / Fuel Filters",
        "manufacturer": "NAPA",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.napaonline.com/en/p/FIL3481",
        "notes": "NAPA lists FIL 3481 as a NAPA Gold fuel filter.",
    },
    "600-319-8610": {
        "proposed_name": "Filter Element - Komatsu 600-319-8610",
        "family": "Filters",
        "category": "Parts / Filters",
        "manufacturer": "Komatsu",
        "confidence": "Needs Review",
        "action": "Review before update",
        "source": "https://www.google.com/search?q=%22600-319-8610%22+Komatsu+filter",
        "notes": "Search result quality was not strong enough in this pass; likely Komatsu filter family but needs confirmation.",
    },
    "6735-51-5140": {
        "proposed_name": "Engine Oil Filter - Komatsu 6735-51-5140",
        "family": "Engine Oil Filters",
        "category": "Parts / Filters / Engine Oil Filters",
        "manufacturer": "Komatsu",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.komatsu.com/en-us/products/parts/filters/6735-51-5140",
        "notes": "Komatsu lists 6735-51-5140 as an Engine Oil Filter with element style.",
    },
    "6754-79-6140": {
        "proposed_name": "Fuel Filter - Komatsu 6754-79-6140",
        "family": "Fuel Filters",
        "category": "Parts / Filters / Fuel Filters",
        "manufacturer": "Komatsu",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://store.kmpbrand.com/us/filter-fuel-6754-79-6140",
        "notes": "KMP Brand and other supplier pages list 6754-79-6140 as FILTER FUEL/Fuel Filter for Komatsu.",
    },
    "422HC": {
        "proposed_name": "Rotary Cutter Blade CCW - Howse 422HC",
        "family": "Ground Engaging Tools",
        "category": "Parts / Ground Engaging Tools",
        "manufacturer": "A&I Products",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://farmpartsstore.com/rotary-cutter-c-clock-wise-blade-422hc/",
        "notes": "Farm Parts Store lists 422HC as a Howse rotary cutter CCW blade by A&I Products, with dimensions and fitment notes.",
    },
    "V0621-65150": {
        "proposed_name": "Hydraulic Return Filter - Kubota V0621-65150",
        "family": "Hydraulic Filters",
        "category": "Parts / Filters / Hydraulic Filters",
        "manufacturer": "Kubota",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.colemanequip.com/parts/details/KubotaParts/Return-Filter/V0621-65150/",
        "notes": "Coleman Equipment and multiple Kubota dealer pages list this as a Kubota Return/Hydraulic Return Filter.",
    },
    "9239529": {
        "proposed_name": "Top Carrier Roller - 9239529",
        "family": "Undercarriage",
        "category": "Parts / Undercarriage",
        "manufacturer": "",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.conequip.com/9239529",
        "notes": "Multiple parts sources identify 9239529 as a top/carrier roller for mini excavators across Deere, Hitachi, Kubota, Komatsu, and related applications.",
    },
    "CS24314 DRIVE SHAFT": {
        "proposed_name": "Rotary Cutter Driveline - Weasler CS24314 - 51 in",
        "family": "Driveline",
        "category": "Parts / Driveline",
        "manufacturer": "Weasler",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.qualityfarmsupply.com/products/metric-driveline-bypy-series-2-51-compressed-length-for-rotary-cutter-general-application",
        "notes": "Quality Farm Supply lists a Weasler 51 in rotary cutter driveline with interchange number CS24314.",
    },
    "RS3988": {
        "proposed_name": "Engine Air Filter - Baldwin RS3988",
        "family": "Air Filters",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "Baldwin",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.grainger.com/product/BALDWIN-FILTERS-Automotive-Air-Filter-Round-2KYW7",
        "notes": "Grainger lists Baldwin RS3988 as an Automotive/Engine Air Filter.",
    },
    "550019913": {
        "proposed_name": "Multi-Purpose Lubricant and Penetrant - Shell Rotella 550019913",
        "family": "Lubricants",
        "category": "Parts / Lubricants",
        "manufacturer": "Shell Rotella",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.fleetpride.com/parts/shell-rotella-multi-purpose-lubricant-and-penetrant-550019913",
        "notes": "FleetPride lists Shell Rotella 550019913 as Multi-Purpose Lubricant and Penetrant.",
    },
    "50X90X10": {
        "proposed_name": "Oil Seal - 50 x 90 x 10 mm",
        "family": "Oil Seals",
        "category": "Parts / Seals / Oil Seals",
        "manufacturer": "",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.motion.com/products/sku/00612208",
        "notes": "Motion lists 50X90X10 as an oil seal with 50 mm shaft, 90 mm OD, 10 mm width.",
    },
    "76824": {
        "proposed_name": "Air Filter Outer - Sparex S.76824",
        "family": "Air Filters",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "Sparex",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://us.sparex.com/air-filter-outer-76824.html",
        "notes": "Sparex lists S.76824 as an outer air filter with dimensions and OEM cross references.",
    },
    "S.38": {
        "proposed_name": "Linch Pin - Sparex S.38 - 8 x 44.5 mm",
        "family": "Hardware",
        "category": "Parts / Hardware",
        "manufacturer": "Sparex",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://farmingparts.com/collections/pto-linch-pins-1/products/round-linch-pin-pin-8mm-x-44-5mm-s-38",
        "notes": "Sparex reseller and CAD catalog identify S.38 as a round linch pin, 8 mm x 44.5 mm.",
    },
    "86304": {
        "proposed_name": "Fuel/Water Separator - Carquest 86304",
        "family": "Fuel Water Separators",
        "category": "Parts / Filters / Fuel Water Separators",
        "manufacturer": "Carquest",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://www.fleetpride.com/parts/donaldson-fuel-filter/01tUZ000001pGhiYAE",
        "notes": "FleetPride cross-reference list ties FCS Auto/Carquest 86304 to Donaldson P551434 fuel/water separator cartridge.",
    },
    "88375": {
        "proposed_name": "Secondary Air Filter - Carquest 88375",
        "family": "Air Filters",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "Carquest",
        "confidence": "High",
        "action": "Rename and categorize",
        "source": "https://shop.advanceautoparts.com/p/carquest-premium-secondary-air-filter-superior-protection-for-clean-engine-air-intake-systems-88375/10557621-P",
        "notes": "Advance Auto Parts lists Carquest 88375 as a premium secondary air filter for Bobcat, Gehl, John Deere equipment and Tymco sweepers.",
    },
    "AT31547": {
        "proposed_name": "",
        "family": "",
        "category": "",
        "manufacturer": "",
        "confidence": "Low",
        "action": "Needs web/manual review",
        "source": "https://www.google.com/search?q=%22AT31547%22+%22John+Deere%22",
        "notes": "Search results were not reliable for equipment parts in this pass.",
    },
    "PSS3226": {
        "proposed_name": "",
        "family": "",
        "category": "",
        "manufacturer": "",
        "confidence": "Low",
        "action": "Needs web/manual review",
        "source": "https://www.google.com/search?q=%22PSS3226%22+part",
        "notes": "No reliable product identity found in this pass.",
    },
    "B0101616997": {
        "proposed_name": "",
        "family": "",
        "category": "",
        "manufacturer": "",
        "confidence": "Low",
        "action": "Needs web/manual review",
        "source": "https://www.google.com/search?q=%22B0101616997%22+part",
        "notes": "No reliable product identity found in this pass.",
    },
    "60121": {
        "proposed_name": "",
        "family": "",
        "category": "",
        "manufacturer": "",
        "confidence": "Low",
        "action": "Needs web/manual review",
        "source": "https://www.google.com/search?q=%2260121%22+equipment+part",
        "notes": "Search results match unrelated products across industries; do not update automatically.",
    },
    "67580": {
        "proposed_name": "",
        "family": "",
        "category": "",
        "manufacturer": "",
        "confidence": "Low",
        "action": "Needs web/manual review",
        "source": "https://www.google.com/search?q=%2267580%22+equipment+part",
        "notes": "Search results match unrelated products across industries; do not update automatically.",
    },
}


def extract_lookup_key(name: str) -> str:
    upper_name = name.upper().strip()
    if upper_name.startswith("OEM PART "):
        return name[9:].strip().upper()
    return upper_name


def main() -> None:
    rows = []
    seen = set()
    with AUDIT.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["Issue"] not in {"Generic Product Name", "No Vendor Line"}:
                continue
            key = extract_lookup_key(row["Name"])
            if key not in WEB_FINDINGS:
                continue
            dedupe_key = (row["Product ID"], key)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            finding = WEB_FINDINGS[key]
            rows.append(
                {
                    "Product ID": row["Product ID"],
                    "Internal Reference": row["Internal Reference"],
                    "Current Name": row["Name"],
                    "Current Category": row["Category"],
                    "Lookup Key": key,
                    "Proposed Name": finding["proposed_name"],
                    "Product Family": finding["family"],
                    "Proposed Category": finding["category"],
                    "Manufacturer": finding["manufacturer"],
                    "Confidence": finding["confidence"],
                    "Recommended Action": finding["action"],
                    "Source URL": finding["source"],
                    "Notes": finding["notes"],
                }
            )

    fieldnames = [
        "Product ID",
        "Internal Reference",
        "Current Name",
        "Current Category",
        "Lookup Key",
        "Proposed Name",
        "Product Family",
        "Proposed Category",
        "Manufacturer",
        "Confidence",
        "Recommended Action",
        "Source URL",
        "Notes",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = {}
    for row in rows:
        counts[row["Confidence"]] = counts.get(row["Confidence"], 0) + 1
    print(f"Wrote {len(rows)} web cleanup opportunities to {OUT}")
    print(counts)


if __name__ == "__main__":
    main()
