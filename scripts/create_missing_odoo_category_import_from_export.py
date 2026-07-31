import csv
import re
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
MASTER = PRODUCT_MASTER / "import_ready" / "master_products.csv"
SAFE_IMPORT = PRODUCT_MASTER / "import_ready" / "odoo_exact_schema_safe_import.csv"
CATEGORY_EXPORT = Path.home() / "Downloads" / "Product Category (product.category).xlsx"
IMPORT_DIR = PRODUCT_MASTER / "import_ready"
REVIEW_DIR = PRODUCT_MASTER / "review_reports"

CATEGORY_FIELDS = [
    "Name",
    "Parent Category",
    "Costing Method",
    "Inventory Valuation",
    "Income Account",
    "Expense Account",
    "Stock Valuation Account",
    "Stock Variation Account",
]

AUDIT_FIELDS = [
    "Display Name",
    "Action",
    "Parent Category",
    "Product Count",
]


def clean(value):
    return re.sub(r"\s+", " ", str(value or "").strip())


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def category_paths(category):
    parts = [clean(part) for part in re.split(r"\s*/\s*", category or "") if clean(part)]
    return [" / ".join(parts[:i]) for i in range(1, len(parts) + 1)]


def parent_path(display_name):
    parts = [clean(part) for part in re.split(r"\s*/\s*", display_name or "") if clean(part)]
    return " / ".join(parts[:-1])


def leaf_name(display_name):
    parts = [clean(part) for part in re.split(r"\s*/\s*", display_name or "") if clean(part)]
    return parts[-1] if parts else ""


def main():
    product_source = SAFE_IMPORT if SAFE_IMPORT.exists() else MASTER
    master_rows = read_csv(product_source)
    category_counts = {}
    for row in master_rows:
        category = clean(row.get("Product Category", ""))
        if category:
            category_counts[category] = category_counts.get(category, 0) + 1

    needed = set()
    for category in category_counts:
        needed.update(category_paths(category))

    export = pd.read_excel(CATEGORY_EXPORT, dtype=str).fillna("")
    existing = {clean(value) for value in export["Display Name"].tolist()}
    parts_row = export[export["Display Name"].map(clean) == "Parts"]
    if parts_row.empty:
        raise SystemExit("The Odoo category export does not contain a root 'Parts' category.")
    parts_defaults = {field: clean(parts_row.iloc[0].get(field, "")) for field in CATEGORY_FIELDS if field != "Name"}

    missing = sorted(needed - existing, key=lambda value: (value.count(" / "), value))
    create_rows = []
    audit_rows = []
    for display_name in missing:
        parent = parent_path(display_name)
        row = {
            **parts_defaults,
            "Name": leaf_name(display_name),
            "Parent Category": parent,
        }
        create_rows.append(row)
        audit_rows.append({
            "Display Name": display_name,
            "Action": "Create",
            "Parent Category": parent,
            "Product Count": str(category_counts.get(display_name, 0)),
        })

    for display_name in sorted(existing & needed):
        audit_rows.append({
            "Display Name": display_name,
            "Action": "Already exists",
            "Parent Category": parent_path(display_name),
            "Product Count": str(category_counts.get(display_name, 0)),
        })

    write_csv(IMPORT_DIR / "odoo_missing_product_categories_create.csv", create_rows, CATEGORY_FIELDS)
    write_csv(REVIEW_DIR / "odoo_category_existing_vs_needed_audit.csv", audit_rows, AUDIT_FIELDS)
    print(f"Needed category paths: {len(needed)}")
    print(f"Already existing category paths: {len(existing & needed)}")
    print(f"Missing category import rows: {len(create_rows)}")


if __name__ == "__main__":
    main()
