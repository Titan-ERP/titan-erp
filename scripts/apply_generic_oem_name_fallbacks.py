import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_PATHS = [
    ROOT / "outputs" / "southern_equipment_master_parts_database_v1" / "master_products.csv",
    ROOT / "odoo_imports" / "product_master" / "import_ready" / "master_products.csv",
]


FALLBACKS = {
    "o-ring": ("O-Ring", "Hardware", "Hardware"),
    "seal kit": ("Seal Kit", "Hydraulic Seal Kits", "Seals / Hydraulic Seal Kits"),
    "gasket": ("Gasket", "Engine", "Engine / Gaskets"),
    "bushing": ("Bushing", "Hardware", "Hardware"),
    "hose": ("Hose", "Hydraulic Hoses", "Hydraulic / Hydraulic Hoses"),
    "key": ("Key", "Hardware", "Hardware"),
    "ring": ("Ring", "Hardware", "Hardware"),
    "pin": ("Pin", "Hardware", "Hardware"),
    "washer": ("Washer", "Hardware", "Hardware"),
    "cap": ("Cap", "Hardware", "Hardware"),
}


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


def clean_oem(oem):
    return re.sub(r"\s+", " ", (oem or "").strip())


def main():
    total = 0
    for path in MASTER_PATHS:
        if not path.exists():
            continue
        rows = read_csv(path)
        changed = 0
        for row in rows:
            if row.get("Status") != "Needs Review":
                continue
            original = (row.get("Original Product Name") or "").strip().lower()
            oem = clean_oem(row.get("OEM Part Number", ""))
            if not oem or original not in FALLBACKS:
                continue
            base_name, family, category = FALLBACKS[original]
            name = f"{base_name} - {oem}"
            original_notes = row.get("Notes", "").strip()
            row["Product Name"] = name
            row["Product Family"] = family
            row["Product Category"] = category
            row["Status"] = "Approved"
            row["Confidence"] = "0.80"
            row["Sales Description"] = name
            row["Purchase Description"] = f"{name} | OEM: {oem}"
            row["Notes"] = "; ".join(
                x for x in [original_notes, "fallback generic name made searchable with OEM number"] if x
            )
            changed += 1
        write_csv(path, rows, rows[0].keys())
        print(f"{path}: fallback-cleaned {changed} rows")
        total += changed
    print(f"Total fallback-cleaned rows across files: {total}")


if __name__ == "__main__":
    main()
