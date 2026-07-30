import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
MASTER = PRODUCT_MASTER / "import_ready" / "master_products.csv"
IMPORT_DIR = PRODUCT_MASTER / "import_ready"
REVIEW_DIR = PRODUCT_MASTER / "review_reports"

ARCHIVE_FIELDS = ["ID", "Active"]
AUDIT_FIELDS = [
    "ID",
    "Internal Reference",
    "OEM Part Number",
    "Name",
    "Product Category",
    "Manufacturer",
    "Archive Action",
    "Reason",
]
SYSTEM_PROTECTED_PATTERNS = [
    "sale_timesheet.",
    "pos_settle_due.",
    "point_of_sale.",
]


def is_system_protected(row):
    external_id = (row.get("External ID") or "").strip()
    name = (row.get("Product Name") or "").strip().lower()
    category = (row.get("Product Category") or "").strip().lower()
    if any(external_id.startswith(pattern) for pattern in SYSTEM_PROTECTED_PATTERNS):
        return True
    if "service on timesheets" in name:
        return True
    if category == "services":
        return True
    return False


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def sanitize(value):
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([{key: sanitize(value) for key, value in row.items()} for row in rows])


def to_archive_row(row):
    return {
        "ID": row.get("External ID", ""),
        "Active": "False",
    }


def to_audit_row(row):
    return {
        "ID": row.get("External ID", ""),
        "Internal Reference": row.get("Internal Reference", ""),
        "OEM Part Number": row.get("OEM Part Number", ""),
        "Name": row.get("Product Name", ""),
        "Product Category": row.get("Product Category", ""),
        "Manufacturer": row.get("Manufacturer", ""),
        "Archive Action": "Set Active to False",
        "Reason": row.get("Notes", "") or "Archive candidate held out of safe product import",
    }


def main():
    rows = read_csv(MASTER)
    archive_rows = [row for row in rows if row.get("Status") == "Archive Candidate"]
    protected_rows = [to_audit_row(row) for row in archive_rows if is_system_protected(row)]
    archive_update_rows = [
        to_archive_row(row)
        for row in archive_rows
        if row.get("External ID") and not is_system_protected(row)
    ]
    missing_identifier_rows = [to_audit_row(row) for row in archive_rows if not row.get("External ID")]
    audit_rows = [to_audit_row(row) for row in archive_rows]

    write_csv(IMPORT_DIR / "odoo_archive_products_set_inactive.csv", archive_update_rows, ARCHIVE_FIELDS)
    write_csv(REVIEW_DIR / "odoo_archive_products_audit.csv", audit_rows, AUDIT_FIELDS)
    write_csv(REVIEW_DIR / "odoo_archive_missing_identifier.csv", missing_identifier_rows, AUDIT_FIELDS)
    write_csv(REVIEW_DIR / "odoo_archive_system_protected.csv", protected_rows, AUDIT_FIELDS)

    print(f"Odoo archive import rows: {len(archive_update_rows)}")
    print(f"Odoo archive audit rows: {len(audit_rows)}")
    print(f"Odoo archive rows missing ID: {len(missing_identifier_rows)}")
    print(f"Odoo archive rows blocked as system protected: {len(protected_rows)}")


if __name__ == "__main__":
    main()
