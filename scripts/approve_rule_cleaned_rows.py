import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_PATHS = [
    ROOT / "outputs" / "southern_equipment_master_parts_database_v1" / "master_products.csv",
    ROOT / "odoo_imports" / "product_master" / "import_ready" / "master_products.csv",
]


APPROVE_PATTERNS = [
    r"^Ball Bearing - [A-Za-z0-9 .\-/]+$",
    r"^Hydraulic Adapter - .+",
    r"^Hydraulic Elbow .+",
    r"^Oil Pressure Switch$",
    r"^Park Brake Switch$",
    r"^Pressure Switch$",
    r"^PTO Switch$",
    r"^Push Button Switch$",
    r"^Push Pull Switch$",
    r"^Relay Switch$",
    r"^Rocker Switch$",
    r"^Safety Switch$",
    r"^Starter Solenoid$",
    r"^Flat Washer$",
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


def should_approve(row):
    if row.get("Status") != "Needs Review":
        return False
    name = row.get("Product Name", "").strip()
    if "Needs Review" in name or not name:
        return False
    return any(re.match(pattern, name, flags=re.I) for pattern in APPROVE_PATTERNS)


def main():
    total = 0
    for path in MASTER_PATHS:
        if not path.exists():
            continue
        rows = read_csv(path)
        changed = 0
        for row in rows:
            if should_approve(row):
                original_notes = row.get("Notes", "").strip()
                row["Status"] = "Approved"
                row["Confidence"] = "0.90"
                row["Notes"] = "; ".join(
                    x for x in [original_notes, "rule-approved after standardized name review"] if x
                )
                changed += 1
        write_csv(path, rows, rows[0].keys())
        print(f"{path}: rule-approved {changed} rows")
        total += changed
    print(f"Total rule-approved rows across files: {total}")


if __name__ == "__main__":
    main()
