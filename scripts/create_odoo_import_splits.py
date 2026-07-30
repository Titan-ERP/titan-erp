import csv
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
MASTER = PRODUCT_MASTER / "import_ready" / "master_products.csv"
IMPORT_DIR = PRODUCT_MASTER / "import_ready"
REVIEW_DIR = PRODUCT_MASTER / "review_reports"
DOC_DIR = PRODUCT_MASTER / "documentation"


ODOO_FIELDS = [
    ("External ID", "id", "Use for safest updates when importing back into the same Odoo database."),
    ("Product Name", "name", "Cleaned product name shown in Odoo."),
    ("Internal Reference", "default_code", "Southern/Odoo internal SKU. Preserved from source."),
    ("Barcode", "barcode", "Preserved from source."),
    ("Product Category", "categ_id/id or categ_id", "Suggested category. Confirm category import style in Odoo before importing."),
    ("Product Type", "detailed_type or type", "Source value is Goods. Odoo version may use a different technical field."),
    ("Sales Price", "list_price", "Preserved from source."),
    ("Cost", "standard_price", "Preserved from source."),
    ("Unit of Measure", "uom_id", "Source value is Units."),
    ("Purchase Description", "description_purchase", "Generated buyer-facing description."),
    ("Sales Description", "description_sale", "Generated sales-facing description."),
    ("sale_ok", "sale_ok", "Preserved from source."),
    ("purchase_ok", "purchase_ok", "Preserved from source."),
    ("is_storable", "is_storable", "Preserved from source where supported by Odoo version."),
    ("Routes", "Routes", "Preserved from source; test in small batch."),
    ("Sales Taxes", "taxes_id/name", "Preserved from source; test in small batch."),
    ("Purchase Taxes", "supplier_taxes_id/name", "Preserved from source."),
    ("Manufacturer", "x_manufacturer or custom field", "Not a standard Odoo product field unless configured."),
    ("OEM Part Number", "x_oem_part_number or custom field", "Not a standard Odoo product field unless configured."),
    ("Product Family", "x_product_family or tag/category helper", "Not standard; use custom field or do not import."),
    ("Search Keywords", "description or custom search field", "Useful, but choose destination before importing."),
    ("Status", "Do not import by default", "Governance field for this cleanup project."),
    ("Confidence", "Do not import by default", "Governance field for this cleanup project."),
    ("Notes", "Do not import by default", "Audit notes; keep in CSV unless you create an internal note field."),
]


SAFE_IMPORT_FIELDS = [
    "External ID",
    "Internal Reference",
    "OEM Part Number",
    "Product Name",
    "Product Family",
    "Product Category",
    "Manufacturer",
    "Vendor",
    "Vendor Part Number",
    "Search Keywords",
    "Sales Description",
    "Purchase Description",
    "Product Type",
    "Barcode",
    "Unit of Measure",
    "Cost",
    "Sales Price",
    "Sales Taxes",
    "Purchase Taxes",
    "is_storable",
    "invoice_policy",
    "Routes",
    "sale_ok",
    "purchase_ok",
    "Status",
    "Confidence",
    "Notes",
]


ODOO_MINIMAL_FIELDS = [
    "External ID",
    "Product Name",
    "Internal Reference",
    "Barcode",
    "Product Category",
    "Product Type",
    "Sales Price",
    "Cost",
    "Unit of Measure",
    "Sales Description",
    "Purchase Description",
    "sale_ok",
    "purchase_ok",
]


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_mapping_doc(path):
    lines = [
        "# Odoo Product Import Field Mapping",
        "",
        "Use this document before importing `master_products.csv` or any split file into Odoo.",
        "",
        "## Recommended Import Sequence",
        "",
        "1. Import a small test batch first: `odoo_test_batch_50.csv`.",
        "2. Prefer matching/updating by `External ID` when importing into the same Odoo database.",
        "3. If `External ID` is not accepted, test matching by `Internal Reference`.",
        "4. Do not import rows in `odoo_hold_needs_review.csv` until reviewed.",
        "5. Do not import archive candidates until a business decision is made.",
        "",
        "## Field Mapping",
        "",
        "| CSV Column | Odoo Field | Notes |",
        "|---|---|---|",
    ]
    for csv_col, odoo_field, notes in ODOO_FIELDS:
        lines.append(f"| `{csv_col}` | `{odoo_field}` | {notes} |")
    lines.extend([
        "",
        "## Files Created",
        "",
        "- `import_ready/odoo_safe_import_approved.csv`: approved and web-verified rows only.",
        "- `import_ready/odoo_test_batch_50.csv`: first 50 safe rows for a test import.",
        "- `import_ready/odoo_web_verified_import.csv`: rows confirmed through web/OEM lookup.",
        "- `import_ready/odoo_minimal_safe_import.csv`: smaller Odoo-oriented file with fewer helper columns.",
        "- `review_reports/odoo_hold_needs_review.csv`: rows to hold back from import.",
        "- `review_reports/odoo_archive_candidates_hold.csv`: archive/business-review candidates.",
        "",
        "## Important Warning",
        "",
        "Manufacturer, OEM Part Number, Product Family, Search Keywords, Status, Confidence, and Notes may require custom Odoo fields. Do not map those into production until the destination fields exist.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    rows = read_csv(MASTER)
    write_mapping_doc(DOC_DIR / "odoo_field_mapping.md")

    print(f"Master rows available for exact-schema export: {len(rows)}")
    print("Legacy non-exact split CSVs are no longer generated; use create_odoo_exact_schema_imports.py.")


if __name__ == "__main__":
    main()
