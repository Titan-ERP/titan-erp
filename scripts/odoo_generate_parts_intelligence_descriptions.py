from __future__ import annotations

import argparse
import csv
import html
import os
import socket
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"

INTERNAL_MARKERS = (
    "detail enrichment pending",
    "pricing requires separate review",
    "public blumaq page harvested",
    "source url",
    "harvested",
)


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    socket.setdefaulttimeout(90)
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[int], size: int = 300):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def text_ready(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in INTERNAL_MARKERS)


def short_list(values: list[str], limit: int = 8) -> str:
    clean = []
    seen = set()
    for value in values:
        value = str(value or "").strip()
        if not value or value.lower() in {"unknown", "not provided", "various"}:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        clean.append(value)
    if len(clean) > limit:
        return ", ".join(clean[:limit]) + f", and {len(clean) - limit} more"
    return ", ".join(clean)


def build_html(product: dict[str, Any], specs: list[dict[str, Any]], fitments: list[dict[str, Any]], refs: list[dict[str, Any]], catalogs: list[dict[str, Any]]) -> tuple[str, str]:
    name = product.get("name") or "Replacement part"
    code = product.get("default_code") or ""
    makes = short_list([row.get("make") for row in fitments], 10)
    models = short_list([row.get("model") for row in fitments], 12)
    ref_numbers = short_list([row.get("oem_part_number") for row in refs], 10)
    spec_pairs = []
    for spec in specs[:8]:
        label = str(spec.get("name") or "").strip()
        value = str(spec.get("value") or "").strip()
        unit = str(spec.get("unit") or "").strip()
        if label and value:
            spec_pairs.append((label, f"{value} {unit}".strip()))

    summary_bits = [f"{name}."]
    if code:
        summary_bits.append(f"Reference {code}.")
    if makes:
        summary_bits.append(f"Known fitment data includes {makes}.")
    elif ref_numbers:
        summary_bits.append("Includes OEM cross-reference data for parts-counter lookup.")
    elif spec_pairs:
        summary_bits.append("Includes verified product specification data.")
    summary = " ".join(summary_bits)

    html_parts = [
        '<div class="se-product-summary se-parts-intelligence">',
        f"<p>{html.escape(summary)}</p>",
        "<ul>",
    ]
    if code:
        html_parts.append(f"<li><strong>Reference:</strong> {html.escape(code)}</li>")
    if ref_numbers:
        html_parts.append(f"<li><strong>OEM cross references:</strong> {html.escape(ref_numbers)}</li>")
    if makes:
        html_parts.append(f"<li><strong>Suitable makes:</strong> {html.escape(makes)}</li>")
    if models:
        html_parts.append(f"<li><strong>Known models:</strong> {html.escape(models)}</li>")
    if spec_pairs:
        specs_text = "; ".join(f"{label}: {value}" for label, value in spec_pairs)
        html_parts.append(f"<li><strong>Key specs:</strong> {html.escape(specs_text)}</li>")
    if catalogs:
        catalog_text = short_list(
            [
                " ".join(
                    part
                    for part in [row.get("catalog_code"), row.get("catalog_name"), f"p. {row.get('page_number')}" if row.get("page_number") else ""]
                    if part
                )
                for row in catalogs
            ],
            6,
        )
        if catalog_text:
            html_parts.append(f"<li><strong>Catalog references:</strong> {html.escape(catalog_text)}</li>")
    html_parts.extend(
        [
            "</ul>",
            "<p>Confirm OEM number, dimensions, and machine fitment before ordering.</p>",
            "</div>",
        ]
    )
    sale_description = " ".join(
        part
        for part in [
            summary,
            f"OEM references: {ref_numbers}." if ref_numbers else "",
            f"Suitable makes: {makes}." if makes else "",
            "Confirm OEM number, dimensions, and machine fitment before ordering.",
        ]
        if part
    )
    return "".join(html_parts), sale_description


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate customer-ready descriptions from exact Parts Intelligence records.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--overwrite-ready", action="store_true", help="Overwrite already customer-ready descriptions. Off by default.")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    description_fields = [field for field in ("description_ecommerce", "website_description", "description_sale") if field in fields_get]
    write_description_field = "website_description" if "website_description" in fields_get else description_fields[0]
    type_field = "detailed_type" if "detailed_type" in fields_get else "type"

    domain = [
        (type_field, "!=", "service"),
        "|",
        "|",
        "|",
        "|",
        "|",
        ("southern_specification_ids", "!=", False),
        ("southern_fitment_ids", "!=", False),
        ("southern_oem_reference_ids", "!=", False),
        ("southern_catalog_page_ids", "!=", False),
        ("southern_related_part_ids", "!=", False),
        ("southern_alternate_barcode_ids", "!=", False),
    ]
    product_ids = execute(models, db, uid, api_key, "product.template", "search", [domain], {"order": "id asc", "limit": args.limit or 0})
    if not product_ids:
        print({"mode": "apply" if args.apply else "dry_run", "matched": 0, "updated": 0})
        return 0

    products: dict[int, dict[str, Any]] = {}
    read_fields = ["id", "default_code", "name", type_field] + description_fields
    for id_chunk in chunks(product_ids, 500):
        for product in execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": read_fields}):
            products[product["id"]] = product

    specs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    fitments: dict[int, list[dict[str, Any]]] = defaultdict(list)
    refs: dict[int, list[dict[str, Any]]] = defaultdict(list)
    catalogs: dict[int, list[dict[str, Any]]] = defaultdict(list)

    for model, target, fields in [
        ("southern.parts.specification", specs, ["product_tmpl_id", "name", "value", "unit"]),
        ("southern.parts.fitment", fitments, ["product_tmpl_id", "make_id", "model_id", "engine", "build_list"]),
        ("southern.parts.oem_reference", refs, ["product_tmpl_id", "manufacturer", "oem_part_number"]),
        ("southern.parts.catalog_page", catalogs, ["product_tmpl_id", "catalog_code", "catalog_name", "page_number"]),
    ]:
        rows = execute(models, db, uid, api_key, model, "search_read", [[("product_tmpl_id", "in", product_ids)]], {"fields": fields, "limit": 0})
        for row in rows:
            product_ref = row.get("product_tmpl_id")
            if not isinstance(product_ref, list):
                continue
            product_id = product_ref[0]
            clean = dict(row)
            if "make_id" in clean:
                clean["make"] = clean["make_id"][1] if isinstance(clean.get("make_id"), list) else ""
            if "model_id" in clean:
                clean["model"] = clean["model_id"][1] if isinstance(clean.get("model_id"), list) else ""
            target[product_id].append(clean)

    rows: list[dict[str, Any]] = []
    updated = 0
    for product_id, product in products.items():
        has_ready_description = any(text_ready(product.get(field)) for field in description_fields)
        if has_ready_description and not args.overwrite_ready:
            status = "Skipped - Existing Ready Description"
        else:
            website_description, sale_description = build_html(
                product,
                specs.get(product_id, []),
                fitments.get(product_id, []),
                refs.get(product_id, []),
                catalogs.get(product_id, []),
            )
            values = {
                write_description_field: website_description,
                "description_sale": sale_description,
            }
            if args.apply:
                execute(models, db, uid, api_key, "product.template", "write", [[product_id], values])
                updated += 1
            status = "Updated" if args.apply else "Would Update"
        rows.append(
            {
                "Product ID": product_id,
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Status": status,
                "Specs": len(specs.get(product_id, [])),
                "Fitments": len(fitments.get(product_id, [])),
                "OEM References": len(refs.get(product_id, [])),
                "Catalog Pages": len(catalogs.get(product_id, [])),
            }
        )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"parts_intelligence_description_updates_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Internal Reference", "Name", "Status", "Specs", "Fitments", "OEM References", "Catalog Pages"])
        writer.writeheader()
        writer.writerows(rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "products_with_parts_intelligence": len(product_ids),
            "would_or_did_update": sum(1 for row in rows if row["Status"] in {"Would Update", "Updated"}),
            "updated": updated,
            "description_field": write_description_field,
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
