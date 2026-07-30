"""Refresh Southern Equipment listing comp ranges from authorized Odoo comps.

The command is read-only by default. Pass --apply to write calculated internal
comp fields. It never publishes listings or changes public pricing.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from equipment_comp_analysis import (
    analyze,
    connect_odoo,
    number,
    odoo_call,
    odoo_comps,
    odoo_search_read_all,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs" / "equipment_comp_analysis" / "automatic-refresh"
MODEL = "southern.equipment.listing"
TARGET_COMPANY_ID = 2
TARGET_COMPANY = "Southern Equipment Company (Laurel)"
MINIMUM_COMPS = 3


def listing_for_engine(record: dict) -> dict[str, object]:
    return {
        "Source Listing ID": record.get("source_listing_id") or str(record["id"]),
        "Standardized Title": record.get("public_title") or "",
        "Seller Ask": record.get("seller_ask_price") or record.get("ask_price") or 0,
        "Equipment Type": record.get("equipment_type") or "",
        "Manufacturer": record.get("manufacturer") or "",
        "Model": record.get("model") or "",
        "Year": record.get("year") or "",
        "Hours": record.get("hours") or "",
    }


def build_update(result: dict[str, object]) -> dict[str, object]:
    count = int(result["Comp Count"])
    if count < MINIMUM_COMPS:
        return {
            "comp_low": 0.0,
            "comp_median": 0.0,
            "comp_high": 0.0,
            "comp_count": count,
            "comp_confidence": "low",
            "estimated_market_value": 0.0,
            "deal_score": 0.0,
            "grade": "verify",
            "comp_match_basis": "insufficient",
            "public_deal_summary": result["Public Valuation Summary"],
        }
    return {
        "comp_low": number(result["Comp Low"]),
        "comp_median": number(result["Comp Median"]),
        "comp_high": number(result["Comp High"]),
        "comp_count": count,
        "comp_confidence": result["Comp Confidence"],
        "estimated_market_value": number(result["Comp Median"]),
        "deal_score": number(result["Deal Score"]),
        "grade": result["Grade"],
        "comp_match_basis": result["Comp Match Basis"],
        "public_deal_summary": result["Public Valuation Summary"],
    }


def comparable(current: dict, desired: dict) -> dict:
    changes = {}
    for field, value in desired.items():
        live = current.get(field)
        if isinstance(value, float):
            if abs(number(live) - value) >= 0.01:
                changes[field] = value
        elif live != value:
            changes[field] = value
    return changes


def write_report(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        if not rows:
            handle.write("")
            return
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--company-id", type=int, default=TARGET_COMPANY_ID)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    connection = connect_odoo()
    if args.apply:
        # Production Odoo is authoritative. Delegating the mutation here keeps
        # this CLI useful to automations without allowing its offline fallback
        # heuristics to overwrite native specification-based results.
        odoo_call(
            connection,
            MODEL,
            "action_recalculate_all_comp_analysis",
            [[]],
        )
        print(f"COMPANY_ID={args.company_id}")
        print(f"COMPANY={TARGET_COMPANY}")
        print("MODE=NATIVE_ODOO_DELEGATION")
        print("APPLIED=NATIVE_RECALCULATION")
        return 0
    listings = odoo_search_read_all(
        connection,
        MODEL,
        [
            ("company_id", "=", args.company_id),
            ("public_status", "not in", ["archived", "sold"]),
        ],
        [
            "id",
            "source_listing_id",
            "public_title",
            "equipment_type",
            "manufacturer",
            "model",
            "year",
            "hours",
            "seller_ask_price",
            "ask_price",
            "comp_low",
            "comp_median",
            "comp_high",
            "comp_count",
            "comp_confidence",
            "estimated_market_value",
            "deal_score",
            "grade",
            "comp_match_basis",
            "public_deal_summary",
        ],
    )
    comps = odoo_comps(connection, company_id=args.company_id)

    report = []
    planned = []
    for record in listings:
        result = analyze(listing_for_engine(record), comps)
        desired = build_update(result)
        changes = comparable(record, desired)
        planned.append((record["id"], changes))
        report.append(
            {
                "Odoo ID": record["id"],
                "Source Listing ID": record.get("source_listing_id") or "",
                "Public Title": record.get("public_title") or "",
                "Comp Low": result["Comp Low"],
                "Comp Median": result["Comp Median"],
                "Comp High": result["Comp High"],
                "Comp Count": result["Comp Count"],
                "Comp Confidence": result["Comp Confidence"],
                "Comp Match Basis": result["Comp Match Basis"],
                "Deal Score": result["Deal Score"],
                "Grade": result["Grade"],
                "Eligible": int(result["Comp Count"]) >= MINIMUM_COMPS,
                "Will Update": bool(changes),
                "Changed Fields": "|".join(sorted(changes)),
            }
        )

    if args.apply:
        grouped: dict[tuple, list[int]] = {}
        updates: dict[tuple, dict] = {}
        for record_id, changes in planned:
            if not changes:
                continue
            key = tuple(sorted(changes.items()))
            grouped.setdefault(key, []).append(record_id)
            updates[key] = changes
        for key, record_ids in grouped.items():
            odoo_call(connection, MODEL, "write", [record_ids, updates[key]])

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output_dir / f"automatic-comp-refresh-{stamp}.csv"
    write_report(output, report)
    eligible = sum(int(row["Eligible"]) for row in report)
    changed = sum(bool(changes) for _record_id, changes in planned)
    print(f"COMPANY_ID={args.company_id}")
    print(f"COMPANY={TARGET_COMPANY}")
    print(f"LISTINGS={len(listings)}")
    print(f"COMPS={len(comps)}")
    print(f"ELIGIBLE={eligible}")
    print(f"CHANGED={changed}")
    print(f"APPLIED={changed if args.apply else 0}")
    print(f"OUTPUT={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
