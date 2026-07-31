from __future__ import annotations

import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"


def load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def odoo_connect():
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


CAPABILITIES = [
    ("Internal reference / SKU", ["default_code"]),
    ("Primary barcode", ["barcode"]),
    ("Product photo", ["image_1920", "image_1024"]),
    ("Sales description", ["description_sale"]),
    ("Purchase description", ["description_purchase"]),
    ("Website description", ["website_description"]),
    ("Internal category", ["categ_id"]),
    ("Website category", ["public_categ_ids"]),
    ("Vendor price lines", ["seller_ids"]),
    ("Product attributes / variants", ["attribute_line_ids", "product_template_attribute_value_ids"]),
    ("Accessory / related sale products", ["accessory_product_ids", "optional_product_ids", "alternative_product_ids"]),
    ("HS / tariff code", ["hs_code", "intrastat_code_id", "commodity_code"]),
    ("Manufacturer / brand", ["manufacturer_id", "brand_id", "product_brand_id", "x_manufacturer", "x_studio_manufacturer"]),
    (
        "Multiple OEM cross references",
        [
            "southern_oem_reference_ids",
            "oem_reference_ids",
            "x_oem_reference_ids",
            "x_studio_oem_part_numbers",
        ],
    ),
    (
        "Make/model fitment records",
        [
            "southern_fitment_ids",
            "fitment_ids",
            "x_fitment_ids",
            "x_studio_fitment",
        ],
    ),
    (
        "Specification key/value records",
        [
            "southern_specification_ids",
            "specification_ids",
            "x_specification_ids",
            "x_studio_specifications",
        ],
    ),
    (
        "Catalog page references",
        [
            "southern_catalog_page_ids",
            "catalog_page_ids",
            "x_catalog_page_ids",
            "x_studio_catalog_pages",
        ],
    ),
    (
        "Alternate barcodes",
        [
            "southern_alternate_barcode_ids",
            "barcode_ids",
            "product_barcode_ids",
            "x_alternate_barcode_ids",
        ],
    ),
    (
        "Source URL / supplier source",
        [
            "southern_source_url",
            "source_url",
            "x_source_url",
            "x_studio_source_url",
        ],
    ),
]


MODULE_NAMES = [
    "product",
    "website_sale",
    "stock",
    "purchase",
    "sale",
    "repair",
    "industry_fsm",
    "sale_renting",
    "rental",
    "website_sale_stock",
    "product_brand",
    "southern_parts_intelligence",
]


def main() -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    db, uid, api_key, models = odoo_connect()

    product_fields = models.execute_kw(
        db,
        uid,
        api_key,
        "product.template",
        "fields_get",
        [],
        {"attributes": ["string", "type", "relation", "readonly", "store"]},
    )

    capability_rows = []
    for capability, candidates in CAPABILITIES:
        present = [field for field in candidates if field in product_fields]
        capability_rows.append(
            {
                "capability": capability,
                "status": "Present" if present else "Missing",
                "matching_fields": "; ".join(present),
                "candidate_fields_checked": "; ".join(candidates),
            }
        )

    custom_product_fields = []
    for name, meta in sorted(product_fields.items()):
        label = meta.get("string") or ""
        lowered = f"{name} {label}".lower()
        if name.startswith("x_") or any(
            term in lowered
            for term in [
                "manufacturer",
                "brand",
                "oem",
                "fitment",
                "catalog",
                "spec",
                "tariff",
                "hs code",
                "barcode",
                "related",
            ]
        ):
            custom_product_fields.append(
                {
                    "field": name,
                    "label": label,
                    "type": meta.get("type", ""),
                    "relation": meta.get("relation", ""),
                    "readonly": meta.get("readonly", ""),
                    "store": meta.get("store", ""),
                }
            )

    installed_modules = models.execute_kw(
        db,
        uid,
        api_key,
        "ir.module.module",
        "search_read",
        [[["name", "in", MODULE_NAMES], ["state", "=", "installed"]]],
        {"fields": ["name", "shortdesc", "state"], "limit": 100},
    )

    interesting_models = models.execute_kw(
        db,
        uid,
        api_key,
        "ir.model",
        "search_read",
        [
            [
                "|",
                "|",
                "|",
                "|",
                ("model", "ilike", "fit"),
                ("model", "ilike", "oem"),
                ("model", "ilike", "catalog"),
                ("model", "ilike", "spec"),
                ("model", "ilike", "southern"),
            ]
        ],
        {"fields": ["model", "name", "state"], "limit": 200},
    )

    counts = {
        "products": models.execute_kw(db, uid, api_key, "product.template", "search_count", [[]]),
        "published_products": models.execute_kw(
            db, uid, api_key, "product.template", "search_count", [[["website_published", "=", True]]]
        )
        if "website_published" in product_fields
        else "n/a",
        "categories": models.execute_kw(db, uid, api_key, "product.category", "search_count", [[]]),
        "website_categories": models.execute_kw(db, uid, api_key, "product.public.category", "search_count", [[]]),
    }

    capability_csv = REPORT_DIR / "odoo_parts_intelligence_capability_audit.csv"
    with capability_csv.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(capability_rows[0].keys()))
        writer.writeheader()
        writer.writerows(capability_rows)

    fields_csv = REPORT_DIR / "odoo_parts_intelligence_relevant_fields.csv"
    with fields_csv.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = ["field", "label", "type", "relation", "readonly", "store"]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(custom_product_fields)

    md_path = REPORT_DIR / "odoo_parts_intelligence_gap_analysis.md"
    missing = [row for row in capability_rows if row["status"] == "Missing"]
    present = [row for row in capability_rows if row["status"] == "Present"]
    md_path.write_text(
        "\n".join(
            [
                "# Odoo Parts Intelligence Gap Analysis",
                "",
                "## Live Odoo Snapshot",
                "",
                f"- Products: {counts['products']}",
                f"- Published products: {counts['published_products']}",
                f"- Internal product categories: {counts['categories']}",
                f"- Website product categories: {counts['website_categories']}",
                "",
                "## What Odoo Already Supports",
                "",
                *[
                    f"- {row['capability']}: `{row['matching_fields']}`"
                    for row in present
                ],
                "",
                "## Missing Sparex-Style Capabilities",
                "",
                *(
                    [
                        f"- {row['capability']}: no matching product.template field found"
                        for row in missing
                    ]
                    or ["- None. All audited capabilities are present."]
                ),
                "",
                "## Installed Relevant Modules",
                "",
                *[
                    f"- `{module['name']}` - {module.get('shortdesc') or ''}"
                    for module in sorted(installed_modules, key=lambda item: item["name"])
                ],
                "",
                "## Existing Related Custom Models",
                "",
                *[
                    f"- `{model['model']}` - {model.get('name') or ''} ({model.get('state') or ''})"
                    for model in sorted(interesting_models, key=lambda item: item["model"])
                ],
                "",
                "## Deployed Parts Intelligence Model",
                "",
                "The dedicated `southern_parts_intelligence` module is deployed. It keeps fitment, cross-reference, specification, catalog-page, alternate-barcode, source, and related-part data in structured fields instead of stuffing it into descriptions or tags.",
                "",
                "### Product Specifications",
                "",
                "Implemented as a child table for key/value specifications: product, group, name, value, unit, source, source URL, and confidence.",
                "",
                "### Make/Model Fitment",
                "",
                "Implemented with normalized make/model records and product fitment relations.",
                "",
                "### OEM Cross References",
                "",
                "Implemented as structured OEM reference records with manufacturer, part number, reference type, source, source URL, and confidence.",
                "",
                "### Catalog Pages",
                "",
                "Implemented as structured catalog references with catalog code/name, page number, source, and source URL.",
                "",
                "### Related Parts",
                "",
                "Implemented as typed related-part relations for alternates, replacements, kit components, accessories, and related products.",
                "",
                "## Website Product Page Tabs",
                "",
                "- Specifications",
                "- Fits Make/Model",
                "- OEM Part Numbers",
                "- Catalog Pages",
                "- Related Parts",
                "",
                "## Import Pipeline Change",
                "",
                "The Sparex and Blumaq harvesters should write product identity, pricing, photos, and publish state to `product.template`, then write detailed specs, fitments, OEM references, catalog pages, and related-part links to the new child models. Keep source and confidence on every harvested detail.",
                "",
                "## Important Data Rule",
                "",
                "Do not create product variants for every make/model fitment. Fitment is compatibility data, not a sellable variant. Variants should remain for actual sellable choices like size, color, package quantity, or configuration.",
                "",
                f"Capability CSV: `{capability_csv}`",
                f"Relevant fields CSV: `{fields_csv}`",
                "",
            ]
        ),
        encoding="utf-8",
    )

    print(f"Wrote {md_path}")
    print(f"Wrote {capability_csv}")
    print(f"Wrote {fields_csv}")
    print(f"Missing capabilities: {len(missing)}")


if __name__ == "__main__":
    main()
