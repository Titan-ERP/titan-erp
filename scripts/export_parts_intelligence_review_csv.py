from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "parts_intelligence"


FIELDS = [
    "run_name",
    "harvested_at",
    "internal_reference",
    "supplier_sku",
    "product_name",
    "vendor",
    "source_url",
    "image_url",
    "evidence_type",
    "evidence_name",
    "evidence_value",
    "manufacturer",
    "make",
    "model",
    "catalog_name",
    "catalog_page",
    "related_internal_reference",
    "relationship_type",
    "category_suggestion",
    "data_confidence",
    "data_status",
    "notes",
]


def records(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("products", "records", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return [payload]
    return []


def clean(value: Any) -> str:
    return "" if value in (None, False) else str(value).strip()


def source_row(run_name: str, record: dict[str, Any], evidence_type: str, status: str, **extra: Any) -> dict[str, str]:
    product = record.get("product") if isinstance(record.get("product"), dict) else {}
    source = record.get("source") if isinstance(record.get("source"), dict) else {}
    row = {field: "" for field in FIELDS}
    row.update(
        {
            "run_name": run_name,
            "harvested_at": clean(source.get("harvested_at")),
            "internal_reference": clean(product.get("internal_reference") or record.get("internal_reference") or record.get("sku")),
            "supplier_sku": clean(product.get("supplier_sku") or product.get("vendor_code")),
            "product_name": clean(product.get("name") or record.get("name") or record.get("title")),
            "vendor": clean(source.get("vendor") or record.get("source_name") or product.get("manufacturer")),
            "source_url": clean(source.get("url") or record.get("source_url") or record.get("url")),
            "image_url": clean(record.get("image_url") or product.get("image_url")),
            "evidence_type": evidence_type,
            "category_suggestion": clean(product.get("category") or record.get("category")),
            "data_confidence": clean(extra.pop("data_confidence", "")),
            "data_status": status,
        }
    )
    for key, value in extra.items():
        if key in row:
            row[key] = clean(value)
    return row


def export(json_path: Path, run_name: str, out_dir: Path) -> tuple[Path, Path, dict[str, int]]:
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    rows: list[dict[str, str]] = []
    counts = {
        "products": 0,
        "source_urls": 0,
        "image_urls": 0,
        "specs": 0,
        "fitments": 0,
        "oem_cross_references": 0,
        "catalog_pages": 0,
        "related_products": 0,
        "category_suggestions": 0,
        "pending_evidence_gaps": 0,
        "total_rows": 0,
    }

    for record in records(payload):
        counts["products"] += 1
        rows.append(
            source_row(
                run_name,
                record,
                "source_url",
                "evidence",
                evidence_name="Vendor/detail page",
                evidence_value=(record.get("source") or {}).get("url") if isinstance(record.get("source"), dict) else record.get("source_url", ""),
                data_confidence="1.0",
            )
        )
        counts["source_urls"] += 1
        if clean(record.get("image_url")):
            rows.append(
                source_row(
                    run_name,
                    record,
                    "image_url",
                    "evidence",
                    evidence_name="Exact product image URL",
                    evidence_value=record.get("image_url"),
                    data_confidence="1.0",
                )
            )
            counts["image_urls"] += 1
        if clean((record.get("product") or {}).get("category") if isinstance(record.get("product"), dict) else record.get("category", "")):
            rows.append(
                source_row(
                    run_name,
                    record,
                    "category_suggestion",
                    "review",
                    evidence_name="Source-derived category suggestion",
                    evidence_value=(record.get("product") or {}).get("category") if isinstance(record.get("product"), dict) else record.get("category", ""),
                    data_confidence="0.7",
                    notes="Review before treating as canonical website taxonomy.",
                )
            )
            counts["category_suggestions"] += 1

        specs = [item for item in record.get("specifications", []) if isinstance(item, dict)]
        for spec in specs:
            rows.append(
                source_row(
                    run_name,
                    record,
                    "product_spec",
                    "evidence",
                    evidence_name=spec.get("name") or spec.get("label"),
                    evidence_value=spec.get("value"),
                    data_confidence=spec.get("confidence", "1.0"),
                    notes=spec.get("group") or spec.get("group_name"),
                )
            )
            counts["specs"] += 1

        oem_refs = [item for item in record.get("oem_references", []) if isinstance(item, dict)]
        for ref in oem_refs:
            rows.append(
                source_row(
                    run_name,
                    record,
                    "oem_cross_reference",
                    "evidence",
                    evidence_name=ref.get("reference_type") or "oem",
                    evidence_value=ref.get("oem_part_number") or ref.get("part_number") or ref.get("value"),
                    manufacturer=ref.get("manufacturer") or ref.get("make"),
                    data_confidence=ref.get("confidence", "1.0"),
                )
            )
            counts["oem_cross_references"] += 1

        fitments = [item for item in record.get("fitments", []) if isinstance(item, dict)]
        for fitment in fitments:
            rows.append(
                source_row(
                    run_name,
                    record,
                    "fitment",
                    "evidence",
                    evidence_name="Make/model fitment",
                    evidence_value=fitment.get("notes"),
                    make=fitment.get("make"),
                    model=fitment.get("model"),
                    data_confidence=fitment.get("confidence", "1.0"),
                )
            )
            counts["fitments"] += 1

        catalogs = [item for item in record.get("catalog_pages", []) if isinstance(item, dict)]
        for catalog in catalogs:
            rows.append(
                source_row(
                    run_name,
                    record,
                    "catalog_page",
                    "evidence",
                    evidence_name=catalog.get("catalog_code") or "Catalog page",
                    catalog_name=catalog.get("catalog_name") or catalog.get("catalog"),
                    catalog_page=catalog.get("page_number") or catalog.get("page"),
                    data_confidence=catalog.get("confidence", "1.0"),
                )
            )
            counts["catalog_pages"] += 1

        related = [item for item in record.get("related_parts", []) if isinstance(item, dict)]
        for item in related:
            rows.append(
                source_row(
                    run_name,
                    record,
                    "related_product",
                    "evidence",
                    evidence_name=item.get("relationship_type") or "related",
                    related_internal_reference=item.get("internal_reference") or item.get("sku") or item.get("default_code"),
                    relationship_type=item.get("relationship_type") or "related",
                    data_confidence=item.get("confidence", "1.0"),
                    notes=item.get("notes"),
                )
            )
            counts["related_products"] += 1

        for label, present in (
            ("fitment", bool(fitments)),
            ("catalog_page", bool(catalogs)),
            ("oem_cross_reference", bool(oem_refs)),
        ):
            if not present:
                rows.append(
                    source_row(
                        run_name,
                        record,
                        label,
                        "not_provided_by_source",
                        evidence_name=label,
                        notes="No evidence harvested from this source page; do not infer.",
                    )
                )
                counts["pending_evidence_gaps"] += 1

    counts["total_rows"] = len(rows)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"parts_intelligence_evidence_review_{run_name}_{timestamp}.csv"
    summary_path = out_dir / f"parts_intelligence_evidence_review_{run_name}_{timestamp}_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    with summary_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["metric", "count"])
        writer.writeheader()
        for key, value in counts.items():
            writer.writerow({"metric": key, "count": value})
    return csv_path, summary_path, counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Export evidence-only parts intelligence review CSVs from harvested JSON.")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--run-name", required=True)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()
    csv_path, summary_path, counts = export(args.json_path, args.run_name, args.out_dir)
    print(f"CSV: {csv_path}")
    print(f"Summary: {summary_path}")
    for key, value in counts.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
