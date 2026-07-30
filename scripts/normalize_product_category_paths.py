import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_PATHS = [
    ROOT / "outputs" / "southern_equipment_master_parts_database_v1" / "master_products.csv",
    ROOT / "odoo_imports" / "product_master" / "import_ready" / "master_products.csv",
]


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([
            {
                key: re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value
                for key, value in row.items()
            }
            for row in rows
        ])


def normalize_category(category):
    category = re.sub(r"\s*/\s*", " / ", (category or "").strip())
    if not category:
        return category
    lowered = category.lower()
    if lowered.startswith("parts / ") or lowered == "parts":
        return category
    if lowered.startswith("services / ") or lowered == "services":
        return category
    if lowered == "service":
        return "Services"
    return f"Parts / {category}"


def main():
    total = 0
    for path in MASTER_PATHS:
        if not path.exists():
            continue
        rows = read_csv(path)
        changed = 0
        for row in rows:
            current = row.get("Product Category", "")
            normalized = normalize_category(current)
            if normalized != current:
                row["Product Category"] = normalized
                changed += 1
        write_csv(path, rows, rows[0].keys())
        print(f"{path}: product category paths normalized {changed}")
        total += changed
    print(f"Total product category paths normalized: {total}")


if __name__ == "__main__":
    main()
