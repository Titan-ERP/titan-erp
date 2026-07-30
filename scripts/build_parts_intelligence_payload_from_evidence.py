from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

import odoo_import_parts_intelligence_json as odoo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = ROOT / "odoo_imports" / "product_master" / "parts_intelligence"


SKIP_NAME_PARTS = {
    "summary",
    "target",
    "targets",
    "report",
    "payload_20260726_1216",
    "batch_20260726_1220",
    "sample_payload",
}


def load_records(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"skip unreadable json {path}: {exc}", file=sys.stderr)
        return []
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("records", "products", "items"):
            if isinstance(payload.get(key), list):
                return [item for item in payload[key] if isinstance(item, dict)]
    return []


def is_candidate(path: Path) -> bool:
    lowered = path.name.lower()
    return not any(part in lowered for part in SKIP_NAME_PARTS)


def first(record: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        value: Any = record
        ok = True
        for part in path.split("."):
            if not isinstance(value, dict) or part not in value:
                ok = False
                break
            value = value[part]
        if ok and value not in (None, "", [], {}):
            return value
    return ""


def split_related(value: str) -> list[str]:
    skus = []
    for match in re.finditer(r"S\.\s*\d+", value or "", flags=re.I):
        skus.append(odoo.clean_sku(match.group(0)))
    return sorted(set(skus))


def has_details(record: dict[str, Any]) -> bool:
    return bool(
        first(record, "specifications", "specs", "product_specifications")
        or first(record, "fitment", "fitments", "suitable_for")
        or first(record, "oem_part_numbers", "oem_references", "oem_refs")
        or first(record, "catalog_pages", "catalogs")
        or first(record, "related_parts", "related_products")
    )


def normalize_record(record: dict[str, Any], source_file: Path) -> tuple[dict[str, Any] | None, dict[str, int]]:
    skipped = {
        "par_reference": 0,
        "empty_sku": 0,
        "unsupported_oem": 0,
        "ambiguous_catalog": 0,
        "empty_spec": 0,
    }
    sku = odoo.clean_sku(str(first(record, "product.internal_reference", "internal_reference", "sku", "default_code")))
    if not sku:
        skipped["empty_sku"] += 1
        return None, skipped
    if sku.startswith("PAR-"):
        skipped["par_reference"] += 1
        return None, skipped

    vendor = first(record, "source.vendor", "source_name") or "Unknown"
    source_url = first(record, "source.url", "source_url")
    normalized: dict[str, Any] = {
        "internal_reference": sku,
        "source": {"vendor": vendor, "url": source_url},
        "enrichment_status": "partial",
        "specifications": [],
        "fitments": [],
        "oem_references": [],
        "catalog_pages": [],
        "related_parts": [],
        "alternate_barcodes": [],
        "evidence_file": str(source_file),
    }

    specs = first(record, "specifications", "specs", "product_specifications")
    if isinstance(specs, dict):
        for name, value in specs.items():
            name = str(name or "").strip()
            value = str(value or "").strip()
            if not name or not value:
                skipped["empty_spec"] += 1
                continue
            if name.lower() == "related products":
                for related_sku in split_related(value):
                    normalized["related_parts"].append(
                        {
                            "internal_reference": related_sku,
                            "relationship_type": "related",
                            "source_name": vendor,
                            "source_url": source_url,
                            "confidence": 1.0,
                        }
                    )
                continue
            normalized["specifications"].append(
                {
                    "group": "Specifications",
                    "name": name,
                    "value": value,
                    "source_name": vendor,
                    "source_url": source_url,
                    "confidence": 1.0,
                }
            )
    elif isinstance(specs, list):
        for item in specs:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or item.get("label") or "").strip()
            value = str(item.get("value") or "").strip()
            if not name or not value:
                skipped["empty_spec"] += 1
                continue
            normalized["specifications"].append(
                {
                    "group": item.get("group") or item.get("group_name") or "Specifications",
                    "name": name,
                    "value": value,
                    "unit": item.get("unit") or "",
                    "source_name": item.get("source_name") or vendor,
                    "source_url": item.get("source_url") or source_url,
                    "confidence": float(item.get("confidence") or 1.0),
                }
            )

    for ref_group in first(record, "oem_part_numbers", "oem_references", "oem_refs") or []:
        if not isinstance(ref_group, dict):
            continue
        manufacturer = str(ref_group.get("make") or ref_group.get("manufacturer") or "").strip()
        if manufacturer.lower() in {"unknown oem", "unknown", ""}:
            skipped["unsupported_oem"] += 1
            continue
        part_numbers = ref_group.get("part_numbers")
        if not isinstance(part_numbers, list):
            part_numbers = [
                ref_group.get("part_number")
                or ref_group.get("oem_part_number")
                or ref_group.get("value")
            ]
        for part_number in part_numbers:
            part_number = str(part_number or "").strip()
            if not part_number or part_number.upper().startswith("PAR-"):
                skipped["unsupported_oem"] += 1
                continue
            normalized["oem_references"].append(
                {
                    "manufacturer": manufacturer,
                    "oem_part_number": part_number,
                    "reference_type": ref_group.get("reference_type") or "oem",
                    "source_name": vendor,
                    "source_url": source_url,
                    "confidence": float(ref_group.get("confidence") or 1.0),
                }
            )

    for fitment in first(record, "fitment", "fitments", "suitable_for") or []:
        if not isinstance(fitment, dict):
            continue
        make = str(fitment.get("make") or "").strip()
        models = fitment.get("models")
        if not isinstance(models, list):
            models = [fitment.get("model")]
        for model in models:
            model = str(model or "").strip()
            if make and model:
                normalized["fitments"].append(
                    {
                        "make": make,
                        "model": model,
                        "engine": fitment.get("engine") or "",
                        "build_list": fitment.get("build_list") or "",
                        "notes": fitment.get("notes") or "",
                        "source_name": vendor,
                        "source_url": source_url,
                        "confidence": float(fitment.get("confidence") or 1.0),
                    }
                )

    for catalog in first(record, "catalog_pages", "catalogs") or []:
        if not isinstance(catalog, dict):
            continue
        catalog_name = str(catalog.get("catalog") or catalog.get("catalog_name") or "").strip()
        if not catalog_name or catalog_name.isdigit():
            skipped["ambiguous_catalog"] += 1
            continue
        pages = catalog.get("pages")
        if not isinstance(pages, list):
            pages = [catalog.get("page") or catalog.get("page_number")]
        for page in pages:
            page = str(page or "").strip()
            if page:
                normalized["catalog_pages"].append(
                    {
                        "catalog_name": catalog_name,
                        "page_number": page,
                        "source_name": vendor,
                        "source_url": source_url,
                    }
                )

    return normalized, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description="Build safe southern Parts Intelligence payloads from exact evidence JSON.")
    parser.add_argument("--roots", nargs="+", type=Path, default=[ROOT / "odoo_imports" / "product_master" / "sparex"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument(
        "--max-child-records",
        type=int,
        default=0,
        help="Skip records with more than this many child rows after normalization. 0 means no cap.",
    )
    args = parser.parse_args()

    normalized_records: list[dict[str, Any]] = []
    skipped_totals: dict[str, int] = {}
    for root in args.roots:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.json")):
            if not is_candidate(path):
                continue
            for record in load_records(path):
                if not has_details(record):
                    continue
                normalized, skipped = normalize_record(record, path)
                for key, value in skipped.items():
                    skipped_totals[key] = skipped_totals.get(key, 0) + value
                if not normalized:
                    continue
                child_count = sum(
                    len(normalized[key])
                    for key in ("specifications", "fitments", "oem_references", "catalog_pages", "related_parts", "alternate_barcodes")
                )
                if args.max_child_records and child_count > args.max_child_records:
                    skipped_totals["over_child_record_cap"] = skipped_totals.get("over_child_record_cap", 0) + 1
                    continue
                normalized_records.append(normalized)

    db, uid, api_key, models = odoo.connect()
    skus = sorted({record["internal_reference"] for record in normalized_records if record.get("internal_reference")})
    existing_skus: set[str] = set()
    for index in range(0, len(skus), 500):
        chunk = skus[index : index + 500]
        rows = odoo.execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "search_read",
            [[("default_code", "in", chunk)]],
            {"fields": ["default_code"], "limit": len(chunk) or 1},
        )
        existing_skus.update(row["default_code"] for row in rows if row.get("default_code"))

    candidates = []
    for normalized in normalized_records:
        if normalized["internal_reference"] not in existing_skus:
            skipped_totals["product_not_found"] = skipped_totals.get("product_not_found", 0) + 1
            continue
        candidates.append(normalized)

    candidates.sort(key=lambda rec: (rec["evidence_file"], rec["internal_reference"]))
    selected = candidates[args.offset : args.offset + args.limit]
    payload = {
        "records": selected,
        "build_notes": {
            "candidate_records_existing_in_odoo": len(candidates),
            "offset": args.offset,
            "limit": args.limit,
            "selected_records": len(selected),
            "skipped": skipped_totals,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload["build_notes"], indent=2))
    print(str(args.output))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
