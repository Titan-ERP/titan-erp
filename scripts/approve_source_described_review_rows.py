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


def clean(value):
    return re.sub(r"\s+", " ", (value or "").strip())


def set_row(row, name, family, category, note, confidence="0.86"):
    oem = clean(row.get("OEM Part Number"))
    original_notes = clean(row.get("Notes"))
    row["Product Name"] = name
    row["Product Family"] = family
    row["Product Category"] = category
    row["Status"] = "Approved"
    row["Confidence"] = confidence
    row["Sales Description"] = name
    row["Purchase Description"] = f"{name}" + (f" | OEM: {oem}" if oem else "")
    row["Notes"] = "; ".join(x for x in [original_notes, note] if x)


def infer(row):
    if row.get("Status") != "Needs Review":
        return False
    oem = clean(row.get("OEM Part Number"))
    original = clean(row.get("Original Product Name"))
    product_name = clean(row.get("Product Name"))
    oem_lower = oem.lower()
    original_lower = original.lower()

    match = re.match(r"^(.+?)\s+bearing$", oem, flags=re.I)
    if match:
        set_row(row, f"Bearing - {clean(match.group(1)).upper()}", "Bearings", "Bearings", "source OEM text includes bearing")
        return True

    match = re.match(r"^(.+?)\s+tail wheel$", oem, flags=re.I)
    if match:
        set_row(row, f"Tail Wheel - {clean(match.group(1)).upper()}", "Hardware", "Hardware", "source OEM text includes tail wheel")
        return True

    if oem_lower == "aw46":
        set_row(row, "Hydraulic Oil AW-46", "Lubricants", "Lubricants", "source OEM text is AW46 lubricant code", "0.92")
        return True

    if oem_lower.startswith("bush-"):
        set_row(row, f"Bushing - {oem.upper()}", "Hardware", "Hardware", "source OEM text includes bushing shorthand")
        return True

    if original_lower == "ign switch":
        set_row(row, f"Ignition Switch - {oem}" if oem else "Ignition Switch", "Electrical", "Electrical", "source name says ignition switch")
        return True

    if original_lower == "switch oil":
        set_row(row, f"Oil Pressure Switch - {oem}" if oem else "Oil Pressure Switch", "Electrical", "Electrical", "source name says oil switch")
        return True

    if original_lower == "override traction switch":
        set_row(row, f"Traction Override Switch - {oem}" if oem else "Traction Override Switch", "Electrical", "Electrical", "source name says override traction switch")
        return True

    if original_lower.startswith("lock pin"):
        suffix = original[8:].strip()
        set_row(row, f"Lock Pin - {suffix}" if suffix else "Lock Pin", "Hardware", "Hardware", "source name says lock pin")
        return True

    if original_lower == "battery":
        set_row(row, "Battery", "Electrical", "Electrical", "source name says battery", "0.82")
        return True

    if original_lower == "pto seal kit":
        set_row(row, f"PTO Seal Kit - {oem}" if oem else "PTO Seal Kit", "Hydraulic Seal Kits", "Seals / Hydraulic Seal Kits", "source name says PTO seal kit")
        return True

    if "korbital motor seal kit" in original_lower:
        set_row(row, f"Korbital Motor Seal Kit - {oem}" if oem else "Korbital Motor Seal Kit", "Hydraulic Seal Kits", "Seals / Hydraulic Seal Kits", "source name says motor seal kit")
        return True

    if product_name == "Hydraulic Adapter" and oem:
        set_row(row, f"Hydraulic Adapter - {oem.upper()}", "Hydraulic Adapters", "Hydraulic / Hydraulic Adapters", "hydraulic adapter kept searchable with OEM number", "0.80")
        return True

    return False


def main():
    total = 0
    for path in MASTER_PATHS:
        if not path.exists():
            continue
        rows = read_csv(path)
        changed = 0
        for row in rows:
            if infer(row):
                changed += 1
        write_csv(path, rows, rows[0].keys())
        print(f"{path}: source-described approvals {changed}")
        total += changed
    print(f"Total source-described approvals across files: {total}")


if __name__ == "__main__":
    main()
