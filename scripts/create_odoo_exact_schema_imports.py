import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
MASTER = PRODUCT_MASTER / "import_ready" / "master_products.csv"
SOURCE = ROOT / "SEC - Overview - Sheet9.csv"
IMPORT_DIR = PRODUCT_MASTER / "import_ready"
REVIEW_DIR = PRODUCT_MASTER / "review_reports"
VENDOR_TBD_PARTNER_EXTERNAL_ID = "__export__.res_partner_333_71aebccf"


ODOO_EXPORT_FIELDS = [
    "ID",
    "Internal Reference",
    "OEM Part Number",
    "Name",
    "Barcode",
    "Product Category",
    "Product Type",
    "Unit",
    "Tags",
    "Cost",
    "Sales Price",
    "Sales Taxes",
    "Purchase Taxes",
    "is_storable",
    "invoice_policy",
    "Routes",
    "sale_ok",
    "purchase_ok",
    "Manufacturer",
    "Vendors/Vendor/External ID",
    "Vendors Product Code",
    "Vendors/price",
    "Vendors/delay",
    "Vendors/min_qty",
    "Income Account",
    "Expense Account",
    "Sub Reference",
]


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=ODOO_EXPORT_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([
            {
                key: re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value
                for key, value in row.items()
            }
            for row in rows
        ])


def write_csv_with_fallback(path, rows, fallback_path=None):
    try:
        write_csv(path, rows)
        return path
    except PermissionError:
        if fallback_path is None:
            raise
        write_csv(fallback_path, rows)
        print(f"{path} is locked; wrote fallback {fallback_path}")
        return fallback_path


def source_key(row):
    return row.get("External ID", "") or row.get("ID", "") or row.get("Internal Reference", "")


def to_odoo_row(row, source_lookup):
    source = source_lookup.get(source_key(row), {})
    vendor = row.get("Vendor", "")
    return {
        "ID": row.get("External ID", ""),
        "Internal Reference": row.get("Internal Reference", ""),
        "OEM Part Number": row.get("OEM Part Number", ""),
        "Name": row.get("Product Name", ""),
        "Barcode": row.get("Barcode", ""),
        "Product Category": row.get("Product Category", ""),
        "Product Type": row.get("Product Type", ""),
        "Unit": row.get("Unit of Measure", ""),
        "Tags": source.get("Tags", ""),
        "Cost": row.get("Cost", ""),
        "Sales Price": row.get("Sales Price", ""),
        "Sales Taxes": row.get("Sales Taxes", ""),
        "Purchase Taxes": row.get("Purchase Taxes", ""),
        "is_storable": row.get("is_storable", ""),
        "invoice_policy": row.get("invoice_policy", ""),
        "Routes": row.get("Routes", ""),
        "sale_ok": row.get("sale_ok", ""),
        "purchase_ok": row.get("purchase_ok", ""),
        "Manufacturer": row.get("Manufacturer", ""),
        "Vendors/Vendor/External ID": VENDOR_TBD_PARTNER_EXTERNAL_ID if vendor else "",
        "Vendors Product Code": row.get("Vendor Part Number", "") if vendor else "",
        "Vendors/price": source.get("Vendors/price", row.get("Cost", "")) if vendor else "",
        "Vendors/delay": source.get("Vendors/delay", "") if vendor else "",
        "Vendors/min_qty": source.get("Vendors/min_qty", "") if vendor else "",
        "Income Account": source.get("Income Account", ""),
        "Expense Account": source.get("Expense Account", ""),
        "Sub Reference": row.get("Sub Reference", ""),
    }


def main():
    rows = read_csv(MASTER)
    source_rows = read_csv(SOURCE)
    source_lookup = {}
    for source in source_rows:
        key = source.get("ID", "") or source.get("Internal Reference", "")
        if key:
            source_lookup[key] = source
    safe = [r for r in rows if r.get("Status") in {"Approved", "Web Verified"}]
    web_verified = [r for r in rows if r.get("Status") == "Web Verified"]
    hold = [r for r in rows if r.get("Status") == "Needs Review"]
    archive = [r for r in rows if r.get("Status") == "Archive Candidate"]

    write_csv(IMPORT_DIR / "odoo_exact_schema_safe_import.csv", [to_odoo_row(r, source_lookup) for r in safe])
    write_csv_with_fallback(
        IMPORT_DIR / "odoo_exact_schema_test_batch_50.csv",
        [to_odoo_row(r, source_lookup) for r in safe[:50]],
        IMPORT_DIR / "odoo_exact_schema_test_batch_50_parts_category.csv",
    )
    write_csv(IMPORT_DIR / "odoo_exact_schema_web_verified.csv", [to_odoo_row(r, source_lookup) for r in web_verified])
    write_csv(REVIEW_DIR / "odoo_exact_schema_hold_needs_review.csv", [to_odoo_row(r, source_lookup) for r in hold])
    write_csv(REVIEW_DIR / "odoo_exact_schema_archive_hold.csv", [to_odoo_row(r, source_lookup) for r in archive])

    print(f"Exact-schema safe import rows: {len(safe)}")
    print(f"Exact-schema test batch rows: {min(len(safe), 50)}")
    print(f"Exact-schema web verified rows: {len(web_verified)}")
    print(f"Exact-schema hold review rows: {len(hold)}")
    print(f"Exact-schema archive hold rows: {len(archive)}")


if __name__ == "__main__":
    main()
