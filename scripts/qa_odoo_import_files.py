import collections
import csv
from pathlib import Path


EXPECTED = [
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


FILES = [
    "odoo_imports/product_master/import_ready/master_products.csv",
    "odoo_imports/product_master/import_ready/odoo_exact_schema_safe_import.csv",
    "odoo_imports/product_master/import_ready/odoo_exact_schema_test_batch_50.csv",
    "odoo_imports/product_master/import_ready/odoo_exact_schema_web_verified.csv",
    "odoo_imports/product_master/review_reports/odoo_exact_schema_hold_needs_review.csv",
    "odoo_imports/product_master/review_reports/odoo_exact_schema_archive_hold.csv",
]


def read_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def read_headers(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        return reader.fieldnames or []


def main():
    failed = False
    for file_name in FILES:
        rows = read_rows(file_name)
        headers = list(rows[0].keys()) if rows else read_headers(file_name)
        exact = headers == EXPECTED if "odoo_exact_schema" in file_name else "n/a"
        extra = sum(1 for row in rows if row.get(None))
        newlines = sum(
            1
            for row in rows
            for value in row.values()
            if isinstance(value, str) and ("\n" in value or "\r" in value)
        )
        blank_names = sum(
            1
            for row in rows
            if not (row.get("Name") or row.get("Product Name") or "").strip()
        )
        print(
            file_name,
            "rows",
            len(rows),
            "exact",
            exact,
            "extra",
            extra,
            "newlines",
            newlines,
            "blank_names",
            blank_names,
        )
        if exact is False or extra or newlines or blank_names:
            failed = True

    safe = read_rows("odoo_imports/product_master/import_ready/odoo_exact_schema_safe_import.csv")
    checks = {
        "safe_duplicate_ids": sum(
            1 for _, count in collections.Counter(row["ID"] for row in safe if row["ID"]).items() if count > 1
        ),
        "safe_duplicate_internal_refs": sum(
            1
            for _, count in collections.Counter(
                row["Internal Reference"] for row in safe if row["Internal Reference"]
            ).items()
            if count > 1
        ),
        "safe_duplicate_oems": sum(
            1
            for _, count in collections.Counter(
                row["OEM Part Number"] for row in safe if row["OEM Part Number"]
            ).items()
            if count > 1
        ),
    }
    for name, value in checks.items():
        print(name, value)
        if value:
            failed = True

    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
