"""Idempotently import normalized equipment comps into Southern Equipment Odoo."""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from equipment_comp_analysis import (
    connect_odoo,
    number,
    odoo_call,
    odoo_search_read_all,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "outputs" / "equipment_comp_analysis" / "manual-auction-comps-20260726.csv"
MODEL = "southern.equipment.comp"
KEY_PREFIX = "manual-auction://"
TARGET_COMPANY = "Southern Equipment Company (Laurel)"


def load_rows(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def values_from_row(row: dict[str, str], company_id: int) -> dict:
    record_id = row["source_record_id"].strip()
    if not record_id:
        raise ValueError("Missing source_record_id")
    if row["result_status"].strip().lower() != "sold":
        raise ValueError(f"{record_id}: only sold results may be imported")
    source_name = row.get("source_name", "").strip()
    if not source_name:
        raise ValueError(f"{record_id}: actual comp source is required")
    price = number(row["total_price"])
    if price <= 0:
        raise ValueError(f"{record_id}: sold price must be positive")
    make = row["make"].strip()
    model = row["model"].strip()
    if not make or not model:
        raise ValueError(f"{record_id}: make and model are required")
    year = int(number(row["year"])) if row["year"].strip() else 0
    category = row["category"].strip()
    equipment_types = {
        "Mini Excavator": ("mini_excavator", "Mini Excavator"),
        "Tracked Excavator": ("excavator", "Tracked Excavator"),
        "Skid Steer Loader": ("skid_steer", "Skid Steer Loader"),
        "Compact Track Loader": ("skid_steer", "Compact Track Loader"),
        "Crawler Dozer": ("dozer", "Crawler Dozer"),
    }
    if category not in equipment_types:
        raise ValueError(f"{record_id}: unsupported equipment category {category!r}")
    equipment_type, equipment_label = equipment_types[category]
    title = " ".join(
        part
        for part in (str(year) if year else "", make, model, equipment_label)
        if part
    )
    name = f"{title} | Lot {row['lot_number'].strip()} | {row['sale_date'].strip()}"
    notes = "\n".join(
        part
        for part in (
            f"Source record ID: {record_id}",
            f"Price basis: {row['price_basis'].strip()}",
            f"Lot number: {row['lot_number'].strip()}",
            f"Condition: {row['condition_note'].strip()}" if row["condition_note"].strip() else "",
            row["meter_note"].strip(),
            f"Provenance: {row['capture_provenance'].strip()}",
            "Source URL was not supplied; no URL was invented.",
            "Raw user-supplied result:",
            row["raw_text"].strip(),
        )
        if part
    )
    values = {
        "name": name,
        "source": source_name,
        "source_url": f"{KEY_PREFIX}{record_id}",
        "equipment_type": equipment_type,
        "manufacturer": make,
        "model": model,
        "price": price,
        "sale_type": "auction_result",
        "sale_date": row["sale_date"].strip() or False,
        "location": row["location"].strip(),
        "notes": notes,
        "company_id": company_id,
    }
    if year:
        values["year"] = year
    hours = number(row["hours"])
    if hours > 0:
        values["hours"] = hours
    return values


def comparable(existing: dict, desired: dict) -> dict:
    changes = {}
    for field, value in desired.items():
        current = existing.get(field)
        if field == "company_id":
            current_id = current[0] if isinstance(current, (list, tuple)) else current
            if int(current_id or 0) != int(value):
                changes[field] = value
        elif field in {"price", "hours"}:
            if abs(number(current) - number(value)) > 0.001:
                changes[field] = value
        elif field == "year":
            if int(current or 0) != int(value or 0):
                changes[field] = value
        elif (current or False) != (value or False):
            changes[field] = value
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    rows = load_rows(args.input)
    connection = connect_odoo()
    company_ids = odoo_call(
        connection,
        "res.company",
        "search",
        [[("name", "=", TARGET_COMPANY)]],
        {"limit": 2},
    )
    if len(company_ids) != 1:
        raise RuntimeError(
            f"Expected exactly one company named {TARGET_COMPANY!r}; found {company_ids}"
        )
    target_company_id = company_ids[0]
    desired = [values_from_row(row, target_company_id) for row in rows]
    if len({row["source_url"] for row in desired}) != len(desired):
        raise RuntimeError("Duplicate source record IDs remain in the normalized input")
    existing_rows = odoo_search_read_all(
        connection,
        MODEL,
        [("source_url", "=like", f"{KEY_PREFIX}%")],
        [
            "name", "source", "source_url", "equipment_type", "manufacturer",
            "model", "year", "hours", "price", "sale_type", "sale_date",
            "location", "notes", "company_id",
        ],
    )
    existing = {row["source_url"]: row for row in existing_rows}
    creates = [row for row in desired if row["source_url"] not in existing]
    updates = []
    for row in desired:
        current = existing.get(row["source_url"])
        if not current:
            continue
        changes = comparable(current, row)
        if changes:
            updates.append((current["id"], changes))
    if args.apply:
        for start in range(0, len(creates), 100):
            odoo_call(connection, MODEL, "create", [creates[start : start + 100]])
        for record_id, changes in updates:
            odoo_call(connection, MODEL, "write", [[record_id], changes])
    live_count = odoo_call(
        connection,
        MODEL,
        "search_count",
        [[("source_url", "=like", f"{KEY_PREFIX}%")]],
    )
    expected_after = len(existing) + len(creates)
    if args.apply and live_count != expected_after:
        raise RuntimeError(f"Post-import count mismatch: expected {expected_after}, found {live_count}")
    print(f"INPUT={len(desired)}")
    print(f"EXISTING={len(existing)}")
    print(f"CREATES={len(creates)}")
    print(f"UPDATES={len(updates)}")
    print(f"APPLIED={int(args.apply)}")
    print(f"LIVE_COUNT={live_count}")
    print(f"TARGET_COMPANY_ID={target_company_id}")
    print(f"TARGET_COMPANY={TARGET_COMPANY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
