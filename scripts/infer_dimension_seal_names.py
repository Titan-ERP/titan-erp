import csv
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MASTER_PATHS = [
    ROOT / "outputs" / "southern_equipment_master_parts_database_v1" / "master_products.csv",
    ROOT / "odoo_imports" / "product_master" / "import_ready" / "master_products.csv",
]


NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)(?:/\d+)?"

DIMENSION_RE = re.compile(
    rf"^\s*({NUMBER})\s*[xX]\s*({NUMBER})\s*[xX]\s*({NUMBER})\s*$"
)


DIMENSION_WITH_PREFIX_RE = re.compile(
    rf"({NUMBER})\s*[xX]\s*({NUMBER})\s*[xX]\s*({NUMBER})"
)


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


def normalize_dim(value):
    value = re.sub(r"(?<=\s)\.(\d+)", r"0.\1", value)
    return re.sub(r"\s+", " ", value.replace("X", "x")).strip()


def infer_name(row):
    if row.get("Status") != "Needs Review":
        return ""
    if row.get("Original Product Name", "").strip().lower() != "seal":
        return ""
    oem = row.get("OEM Part Number", "").strip()
    if not oem:
        return ""
    match = DIMENSION_RE.match(oem) or DIMENSION_WITH_PREFIX_RE.search(oem)
    if not match:
        return ""
    dim = " x ".join(match.groups())
    unit = "in" if "." in dim or "/" in dim else "mm"
    return f"Oil Seal - {normalize_dim(dim)} {unit}"


def main():
    total = 0
    for path in MASTER_PATHS:
        if not path.exists():
            continue
        rows = read_csv(path)
        changed = 0
        for row in rows:
            name = infer_name(row)
            if not name:
                continue
            original_notes = row.get("Notes", "").strip()
            row["Product Name"] = name
            row["Product Family"] = "Oil Seals"
            row["Product Category"] = "Seals / Oil Seals"
            row["Status"] = "Approved"
            row["Confidence"] = "0.88"
            row["Sales Description"] = name
            row["Purchase Description"] = f"{name} | OEM: {row.get('OEM Part Number', '')}"
            row["Notes"] = "; ".join(
                x for x in [original_notes, "dimension-inferred oil seal name"] if x
            )
            changed += 1
        write_csv(path, rows, rows[0].keys())
        print(f"{path}: dimension-inferred {changed} seal rows")
        total += changed
    print(f"Total dimension-inferred rows across files: {total}")


if __name__ == "__main__":
    main()
