import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
MASTER = PRODUCT_MASTER / "import_ready" / "master_products.csv"
IMPORT_DIR = PRODUCT_MASTER / "import_ready"
REVIEW_DIR = PRODUCT_MASTER / "review_reports"

CATEGORY_FIELDS = ["External ID", "Name", "Parent Category/External ID"]
AUDIT_FIELDS = ["Product Category", "External ID", "Name", "Parent Category", "Depth", "Product Count"]


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def clean_part(value):
    return re.sub(r"\s+", " ", value.strip())


def category_id(path):
    slug = re.sub(r"[^a-z0-9]+", "_", path.lower()).strip("_")
    return f"sec_cat_{slug}"


def category_paths(category):
    parts = [clean_part(part) for part in re.split(r"\s*/\s*", category or "") if clean_part(part)]
    paths = []
    for i in range(1, len(parts) + 1):
        paths.append(" / ".join(parts[:i]))
    return paths


def main():
    rows = read_csv(MASTER)
    product_counts = {}
    for row in rows:
        category = clean_part(row.get("Product Category", ""))
        if not category:
            continue
        product_counts[category] = product_counts.get(category, 0) + 1

    all_paths = {}
    for category in product_counts:
        for path in category_paths(category):
            all_paths[path] = True

    category_rows = []
    audit_rows = []
    for path in sorted(all_paths, key=lambda value: (value.count(" / "), value)):
        parts = path.split(" / ")
        parent_path = " / ".join(parts[:-1])
        category_rows.append({
            "External ID": category_id(path),
            "Name": parts[-1],
            "Parent Category/External ID": category_id(parent_path) if parent_path else "",
        })
        audit_rows.append({
            "Product Category": path,
            "External ID": category_id(path),
            "Name": parts[-1],
            "Parent Category": parent_path,
            "Depth": str(len(parts)),
            "Product Count": str(product_counts.get(path, 0)),
        })

    write_csv(IMPORT_DIR / "odoo_product_categories_create.csv", category_rows, CATEGORY_FIELDS)
    write_csv(REVIEW_DIR / "odoo_product_categories_audit.csv", audit_rows, AUDIT_FIELDS)
    print(f"Category import rows: {len(category_rows)}")
    print(f"Leaf categories with products: {len(product_counts)}")


if __name__ == "__main__":
    main()
