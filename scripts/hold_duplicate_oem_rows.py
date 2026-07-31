import csv
import re
from collections import defaultdict
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


def main():
    total = 0
    for path in MASTER_PATHS:
        if not path.exists():
            continue
        rows = read_csv(path)
        by_oem = defaultdict(list)
        for row in rows:
            oem = (row.get("OEM Part Number") or "").strip().lower()
            if oem:
                by_oem[oem].append(row)

        changed = 0
        for members in by_oem.values():
            if len(members) < 2:
                continue
            for row in members:
                if row.get("Status") not in {"Approved", "Web Verified"}:
                    continue
                original_notes = row.get("Notes", "").strip()
                row["Status"] = "Needs Review"
                row["Confidence"] = "0.60"
                row["Notes"] = "; ".join(
                    x for x in [original_notes, "held from safe import due to duplicate OEM Part Number"] if x
                )
                changed += 1

        write_csv(path, rows, rows[0].keys())
        print(f"{path}: duplicate-OEM held {changed} rows")
        total += changed
    print(f"Total duplicate-OEM rows held across files: {total}")


if __name__ == "__main__":
    main()
