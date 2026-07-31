from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def as_records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("products", "records", "items"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
        return [payload]
    return []


def validate_record(record: dict[str, Any], index: int) -> list[str]:
    errors: list[str] = []
    sku = record.get("sku") or record.get("default_code") or record.get("internal_reference")
    product = record.get("product") if isinstance(record.get("product"), dict) else {}
    sku = sku or product.get("internal_reference")
    if not sku:
        errors.append(f"record {index}: missing sku/default_code/internal_reference")

    for key in ("specifications", "fitments", "oem_references", "catalog_pages", "related_parts", "alternate_barcodes"):
        value = record.get(key)
        if value is not None and not isinstance(value, list):
            errors.append(f"record {index}: {key} must be a list")

    for item_index, spec in enumerate(record.get("specifications") or [], 1):
        if not isinstance(spec, dict):
            errors.append(f"record {index} specification {item_index}: must be an object")
            continue
        if not (spec.get("name") or spec.get("label")):
            errors.append(f"record {index} specification {item_index}: missing name")
        if spec.get("value") in (None, ""):
            errors.append(f"record {index} specification {item_index}: missing value")

    for item_index, fitment in enumerate(record.get("fitments") or record.get("suitable_for") or [], 1):
        if not isinstance(fitment, dict):
            errors.append(f"record {index} fitment {item_index}: must be an object")
            continue
        if not fitment.get("make"):
            errors.append(f"record {index} fitment {item_index}: missing make")
        if not fitment.get("model"):
            errors.append(f"record {index} fitment {item_index}: missing model")

    for item_index, ref in enumerate(record.get("oem_references") or record.get("oem_part_numbers") or [], 1):
        if not isinstance(ref, dict):
            errors.append(f"record {index} oem reference {item_index}: must be an object")
            continue
        if not (ref.get("manufacturer") or ref.get("make")):
            errors.append(f"record {index} oem reference {item_index}: missing manufacturer")
        if not (ref.get("oem_part_number") or ref.get("part_number") or ref.get("value")):
            errors.append(f"record {index} oem reference {item_index}: missing part number")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate parts intelligence JSON shape before Odoo import.")
    parser.add_argument("json_path", type=Path)
    args = parser.parse_args()

    payload = json.loads(args.json_path.read_text(encoding="utf-8"))
    records = as_records(payload)
    if not records:
        print("No product records found.", file=sys.stderr)
        return 1

    errors: list[str] = []
    for index, record in enumerate(records, 1):
        errors.extend(validate_record(record, index))

    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

    print(f"payload ok: {len(records)} records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
