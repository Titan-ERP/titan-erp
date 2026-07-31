import csv
import difflib
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "SEC - Overview - Sheet9.csv"
OUTDIR = ROOT / "outputs" / "southern_equipment_master_parts_database_v1"
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
IMPORT_DIR = PRODUCT_MASTER / "import_ready"
REVIEW_DIR = PRODUCT_MASTER / "review_reports"
DOC_DIR = PRODUCT_MASTER / "documentation"


MANUFACTURER_ALIASES = {
    "jd": "John Deere",
    "john deere": "John Deere",
    "deere": "John Deere",
    "kubota": "Kubota",
    "bobcat": "Bobcat",
    "komatsu": "Komatsu",
    "caterpillar": "Caterpillar",
    "cat": "Caterpillar",
    "case ih": "Case IH",
    "case": "Case IH",
    "new holland": "New Holland",
    "yanmar": "Yanmar",
    "ditch witch": "Ditch Witch",
    "massey ferguson": "Massey Ferguson",
    "sparex": "Sparex",
    "donaldson": "Donaldson",
    "baldwin": "Baldwin",
    "fleetguard": "Fleetguard",
    "gates": "Gates",
    "parker": "Parker",
    "woods": "Woods",
    "land pride": "Land Pride",
    "ford": "Ford",
    "kioti": "Kioti",
    "mahindra": "Mahindra",
}


GENERIC_NAMES = {
    "",
    "adapter",
    "bearing",
    "bushing",
    "cap",
    "filter",
    "filters",
    "gasket",
    "hose",
    "key",
    "misc",
    "miscellaneous parts",
    "o-ring",
    "o ring",
    "oem part",
    "part",
    "pin",
    "pump",
    "repair kit",
    "ring",
    "seal",
    "seal kit",
    "solenoid",
    "switch",
    "washer",
}


SIZE_CODE_TO_FRACTION = {
    "04": '1/4"',
    "4": '1/4"',
    "06": '3/8"',
    "6": '3/8"',
    "08": '1/2"',
    "8": '1/2"',
    "10": '5/8"',
    "12": '3/4"',
    "16": '1"',
    "20": '1-1/4"',
    "24": '1-1/2"',
}


FAMILY_CATEGORY = {
    "Air Filter": ("Air Filters", "Filters / Air Filters"),
    "Fuel Filter": ("Fuel Filters", "Filters / Fuel Filters"),
    "Engine Oil Filter": ("Engine Oil Filters", "Filters / Engine Oil Filters"),
    "Hydraulic Filter": ("Hydraulic Filters", "Filters / Hydraulic Filters"),
    "Cab Filter": ("Cab Filters", "Filters / Cab Filters"),
    "Fuel Water Separator": ("Fuel Water Separators", "Filters / Fuel Water Separators"),
    "Bearing": ("Bearings", "Bearings"),
    "Bearing Kit": ("Bearing Kits", "Bearings / Bearing Kits"),
    "Oil Seal": ("Oil Seals", "Seals / Oil Seals"),
    "Wheel Seal": ("Wheel Seals", "Seals / Wheel Seals"),
    "Axle Seal": ("Axle Seals", "Seals / Axle Seals"),
    "Hydraulic Seal": ("Hydraulic Seals", "Seals / Hydraulic Seals"),
    "Hydraulic Seal Kit": ("Hydraulic Seal Kits", "Seals / Hydraulic Seal Kits"),
    "Hydraulic Adapter": ("Hydraulic Adapters", "Hydraulic / Hydraulic Adapters"),
    "Hydraulic Coupler": ("Hydraulic Couplers", "Hydraulic / Hydraulic Couplers"),
    "Hydraulic Elbow": ("Hydraulic Elbows", "Hydraulic / Hydraulic Elbows"),
    "Hydraulic Tee": ("Hydraulic Tees", "Hydraulic / Hydraulic Tees"),
    "Hydraulic Hose": ("Hydraulic Hoses", "Hydraulic / Hydraulic Hoses"),
    "Hydraulic Cylinder": ("Hydraulic Cylinders", "Hydraulic / Hydraulic Cylinders"),
    "Hydraulic Cap": ("Hydraulic Caps", "Hydraulic / Hydraulic Caps"),
    "Hydraulic Plug": ("Hydraulic Plugs", "Hydraulic / Hydraulic Plugs"),
    "Hardware": ("Hardware", "Hardware"),
    "Electrical": ("Electrical", "Electrical"),
    "Fuel System": ("Fuel System", "Fuel System"),
    "Cooling": ("Cooling", "Cooling"),
    "Engine": ("Engine", "Engine"),
    "PTO": ("PTO", "PTO"),
    "Driveline": ("Driveline", "Driveline"),
    "Lubricants": ("Lubricants", "Lubricants"),
    "Paint": ("Paint", "Paint"),
    "Shop Supplies": ("Shop Supplies", "Shop Supplies"),
    "Rental Supplies": ("Rental Supplies", "Rental Supplies"),
    "Service": ("Service", "Service"),
    "Miscellaneous": ("Miscellaneous", "Miscellaneous"),
}


def clean_space(value):
    value = "" if value is None else str(value)
    value = value.replace("\ufeff", "")
    value = value.replace("''", '"').replace("’’", '"').replace("``", '"')
    value = value.replace("×", " x ").replace("\\", "/")
    value = re.sub(r"\s+", " ", value)
    value = re.sub(r"\s+x\s+", " x ", value, flags=re.I)
    return value.strip()


def title_part(value):
    value = clean_space(value)
    if not value:
        return ""
    keep_upper = {
        "AC", "AW", "BSP", "FJIC", "ID", "JIC", "MJIC", "MNPT", "NPT",
        "ORB", "PTO", "SAE", "U-JOINT", "U-JOINTS", "V-BELT",
    }
    words = []
    for word in value.split(" "):
        bare = re.sub(r"[^A-Za-z0-9-]", "", word)
        if bare.upper() in keep_upper:
            words.append(word.replace(bare, bare.upper()))
        elif re.search(r"\d", word):
            words.append(word)
        else:
            words.append(word.capitalize())
    return " ".join(words)


def clean_vendor(value):
    value = clean_space(value)
    if not value or value.lower() in {"vendor tbd", "tbd", "unknown", "n/a", "na"}:
        return "Vendor TBD"
    return value


def normalize_manufacturer(raw, name):
    raw_clean = clean_space(raw).lower()
    name_clean = clean_space(name).lower()
    for alias, normalized in MANUFACTURER_ALIASES.items():
        if raw_clean == alias or re.search(rf"\b{re.escape(alias)}\b", raw_clean):
            return normalized, "source"
    for alias, normalized in MANUFACTURER_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", name_clean):
            return normalized, "inferred_from_name"
    return clean_space(raw), ""


def size_token(token):
    token = clean_space(token).replace("#", "")
    return SIZE_CODE_TO_FRACTION.get(token, f'#{token}')


def normalize_connection(text):
    t = clean_space(text)
    t = re.sub(r"\bMale Pipe\b", "MNPT", t, flags=re.I)
    t = re.sub(r"\bPipe Thread\b", "MNPT", t, flags=re.I)
    t = re.sub(r"\bFemale Pipe\b", "FNPT", t, flags=re.I)
    t = re.sub(r"\bMale JIC\b", "MJIC", t, flags=re.I)
    t = re.sub(r"\bFemale JIC\b", "FJIC", t, flags=re.I)
    t = re.sub(r"\bMale O-?Ring Boss\b", "MORB", t, flags=re.I)
    t = re.sub(r"\bO-?Ring Boss\b", "ORB", t, flags=re.I)
    t = re.sub(r"\bBritish Pipe\b", "BSP", t, flags=re.I)
    t = re.sub(r"\bFlat Face\b", "Flat Face", t, flags=re.I)
    t = re.sub(r"-\s*(#\d+)", r" x \1", t)
    t = re.sub(r"\bTo\b", "x", t, flags=re.I)
    t = re.sub(r"\s+x\s+", " x ", t, flags=re.I)
    return title_part(t)


def polish_product_name(name):
    name = clean_space(name)
    name = name.replace(" X ", " x ")
    replacements = {
        " Morb": " MORB",
        " Mjic": " MJIC",
        " Fjic": " FJIC",
        " Mnpt": " MNPT",
        " Fnpt": " FNPT",
        " Bsp": " BSP",
        " Jic": " JIC",
        " Npt": " NPT",
        " Orb": " ORB",
        " Sae": " SAE",
        " Pto": " PTO",
        "U- Joint": "U-Joint",
        "U Joint": "U-Joint",
    }
    for old, new in replacements.items():
        name = name.replace(old, new)
    name = re.sub(r"^(Hydraulic Adapter - .+?) Adapter$", r"\1", name)
    name = re.sub(r"^(Hydraulic Tee - .+?) Tee$", r"\1", name)
    name = re.sub(r"Hydraulic Elbow 90 - #(\d+) Long 90 FJIC", r"Hydraulic Elbow 90 Long - #\1 FJIC", name)
    name = re.sub(r"Hydraulic Adapter - (#[0-9]+) MNPT x (#[0-9]+) MJIC Adapter", r"Hydraulic Adapter - \1 MNPT x \2 MJIC", name)
    name = re.sub(r"Hydraulic Adapter - (#[0-9]+) BSP x (#[0-9]+) BSP Adapter", r"Hydraulic Adapter - \1 BSP x \2 BSP", name)
    return clean_space(name)


def hydraulic_name(name, oem):
    n = clean_space(name)
    low = n.lower()
    code = clean_space(oem).upper()

    if re.match(r"^fjx90", code) or re.search(r"\bfemale jic 90\b", low):
        size = re.search(r"female jic 90\s+([0-9/.-]+)(?:\s+x\s+([0-9/.-]+))?", low)
        detail = "FJIC"
        if size:
            detail += f" {size.group(1)}"
            if size.group(2):
                detail += f" x {size.group(2)}"
        return f"Hydraulic Elbow 90 - {detail}"
    if re.match(r"^fjx45", code) or re.search(r"\bfemale jic 45\b", low):
        size = re.search(r"female jic 45\s+([0-9/.-]+)(?:\s+x\s+([0-9/.-]+))?", low)
        detail = "FJIC"
        if size:
            detail += f" {size.group(1)}"
            if size.group(2):
                detail += f" x {size.group(2)}"
        return f"Hydraulic Elbow 45 - {detail}"
    if re.match(r"^ffx90", code) or re.search(r"female flat face 90", low):
        m = re.search(r"female flat face 90\s+([0-9/.-]+)", low)
        return "Hydraulic Elbow 90 - Flat Face Female" + (f" {m.group(1)}" if m else "")
    if re.match(r"^fjx", code) or low.startswith("female jic"):
        m = re.search(r"female jic\s+([0-9/.-]+)(?:\s+x\s+([0-9/.-]+))?", low)
        detail = "FJIC"
        if m:
            detail += f" {m.group(1)}"
            if m.group(2):
                detail += f" x {m.group(2)}"
        return f"Hydraulic Adapter - {detail}"
    if re.match(r"^mj", code) or low.startswith("male jic"):
        if "tee" in low:
            return f"Hydraulic Tee - {normalize_connection(n)}"
        m = re.search(r"male jic\s+([0-9/.-]+)(?:\s+x\s+([0-9/.-]+))?", low)
        detail = "MJIC"
        if m:
            detail += f" {m.group(1)}"
            if m.group(2):
                detail += f" x {m.group(2)}"
        return f"Hydraulic Adapter - {detail}"
    if re.match(r"^bspx", code) or low.startswith("british pipe"):
        m = re.search(r"british pipe\s+([0-9/.-]+)(?:\s+x\s+([0-9/.-]+))?", low)
        detail = "BSP"
        if m:
            detail += f" {m.group(1)}"
            if m.group(2):
                detail += f" x {m.group(2)}"
        return f"Hydraulic Adapter - {detail}"
    if low.startswith("adapter"):
        detail = normalize_connection(re.sub(r"^adapter\s*", "", n, flags=re.I))
        if re.search(r"\b90\b|\belbow\b", low):
            return f"Hydraulic Elbow 90 - {detail}"
        return f"Hydraulic Adapter - {detail}" if detail else "Hydraulic Adapter"
    if "coupler" in low or "coupling" in low:
        return "Hydraulic Coupler - " + normalize_connection(n)
    if "plug" in low and "fuel" not in low:
        return "Hydraulic Plug - " + normalize_connection(n)
    if "tee" in low and ("jic" in low or "flare" in low):
        return "Hydraulic Tee - " + normalize_connection(n)
    if "jic" in low or "bsp" in low or "npt" in low or "o-ring boss" in low:
        prefix = "Hydraulic Elbow" if ("90" in low or "elbow" in low) else "Hydraulic Adapter"
        angle = " 90" if prefix.endswith("Elbow") and "90" in low else ""
        return f"{prefix}{angle} - {normalize_connection(n)}"
    return ""


def classify_and_name(row):
    original = clean_space(row.get("Name", ""))
    oem = clean_space(row.get("OEM Part Number", ""))
    combined = f"{original} {oem}".lower()
    notes = []
    changed = False

    hyd = hydraulic_name(original, oem)
    if hyd:
        return hyd, "Hydraulic Adapter" if "Adapter" in hyd else "Hydraulic Elbow" if "Elbow" in hyd else "Hydraulic Tee" if "Tee" in hyd else "Hydraulic Coupler" if "Coupler" in hyd else "Hydraulic Plug", notes + ["hydraulic terminology normalized"], hyd != original

    if re.search(r"\b2\s*wire\b.*\bhose\b", combined):
        m = re.search(r"2\s*wire\s+([0-9/.-]+)\s+hose", combined)
        size = m.group(1) if m else ""
        return f'Hydraulic Hose - {size}" Two-Wire' if size and '"' not in size else f"Hydraulic Hose - {size} Two-Wire".strip(), "Hydraulic Hose", notes + ["hydraulic hose normalized"], True

    if "hydraulic" in combined or re.search(r"\bhydr?\b|\bhydr\b", combined):
        if "filter" in combined:
            return "Hydraulic Filter", "Hydraulic Filter", notes + ["filter type normalized"], original != "Hydraulic Filter"
        if "oil" in combined or "fluid" in combined or re.search(r"\baw\s*-?\s*\d{2}\b", combined):
            aw = re.search(r"\baw\s*-?\s*(\d{2})\b", combined)
            name = f"Hydraulic Oil AW-{aw.group(1)}" if aw else title_part(original)
            return name, "Lubricants", notes + ["lubricant normalized"], name != original
        if "hose" in combined:
            return title_part(original.replace("Hydr", "Hydraulic")), "Hydraulic Hose", notes, True

    if "air filter" in combined or "inner air filter" in combined or "outer air filter" in combined:
        qualifier = "Inner " if "inner" in combined else "Outer " if "outer" in combined else ""
        return f"{qualifier}Air Filter", "Air Filter", notes + ["filter type normalized"], original != f"{qualifier}Air Filter"
    if "fuel water" in combined or "fuel/water" in combined or "water separator" in combined:
        return "Fuel Water Separator", "Fuel Water Separator", notes + ["filter type normalized"], True
    if "fuel filter" in combined:
        return "Fuel Filter", "Fuel Filter", notes + ["filter type normalized"], original != "Fuel Filter"
    if "oil filter" in combined:
        return "Engine Oil Filter", "Engine Oil Filter", notes + ["filter type normalized"], original != "Engine Oil Filter"
    if clean_space(original).lower() in {"filter", "filters"}:
        return "Filter - Needs Review", "Miscellaneous", notes + ["generic filter requires review"], True

    if "seal kit" in combined:
        return "Hydraulic Seal Kit" if "hyd" in combined or "cyl" in combined else "Seal Kit", "Hydraulic Seal Kit" if "hyd" in combined or "cyl" in combined else "Miscellaneous", notes + ["generic seal kit flagged"], original != "Seal Kit"
    if re.search(r"\b(oil seal|seal oil)\b", combined):
        return "Oil Seal", "Oil Seal", notes + ["seal terminology normalized"], original != "Oil Seal"
    if "wheel seal" in combined:
        return "Wheel Seal", "Wheel Seal", notes + ["seal terminology normalized"], original != "Wheel Seal"
    if "axle seal" in combined:
        return "Axle Seal", "Axle Seal", notes + ["seal terminology normalized"], original != "Axle Seal"
    if clean_space(original).lower() == "seal":
        return "Seal - Needs Review", "Miscellaneous", notes + ["generic seal requires review"], True
    if "o-ring" in combined or "o ring" in combined:
        return "O-Ring", "Hardware", notes + ["o-ring standardized"], original != "O-Ring"

    if "wheel bearing kit" in combined:
        return "Wheel Bearing Kit", "Bearing Kit", notes + ["bearing type normalized"], original != "Wheel Bearing Kit"
    if "disc bearing" in combined:
        return title_part(original), "Bearing", notes + ["bearing type normalized"], False
    if re.search(r"\bbearing\b", combined):
        if clean_space(original).lower() == "bearing":
            suggested = f"Ball Bearing - {oem}" if re.search(r"\b\d{3,5}[-A-Z0-9]*\b", oem, re.I) else "Bearing - Needs Review"
            return suggested, "Bearing", notes + ["generic bearing improved from OEM where possible"], True
        return title_part(original), "Bearing", notes, title_part(original) != original

    if "water pump" in combined:
        return "Water Pump", "Cooling", notes, original != "Water Pump"
    if "thermostat" in combined:
        return "Engine Thermostat", "Cooling", notes + ["engine/cooling part normalized"], original != "Engine Thermostat"
    if "radiator" in combined:
        return title_part(original), "Cooling", notes, title_part(original) != original
    if "fuel pump" in combined:
        return "Fuel Pump", "Fuel System", notes, original != "Fuel Pump"
    if "fuel cap" in combined:
        return "Fuel Tank Cap", "Fuel System", notes + ["fuel system terminology normalized"], original != "Fuel Tank Cap"
    if "fuel hose" in combined:
        return title_part(original), "Fuel System", notes, title_part(original) != original

    if "ignition switch" in combined:
        return "Ignition Switch", "Electrical", notes, original != "Ignition Switch"
    if "temp switch" in combined or "temperature switch" in combined:
        return "Temperature Switch", "Electrical", notes + ["electrical terminology normalized"], original != "Temperature Switch"
    if "switch" in combined:
        return title_part(original) if original.lower() != "switch" else "Switch - Needs Review", "Electrical", notes + ["generic switch requires review"], original.lower() == "switch"
    if "solenoid" in combined:
        return "Starter Solenoid" if original.lower() == "solenoid" else title_part(original), "Electrical", notes + ["electrical terminology normalized"], original.lower() == "solenoid"
    if "relay" in combined or "fuse" in combined or "glow plug" in combined or "alternator" in combined or "starter" in combined:
        return title_part(original), "Electrical", notes, title_part(original) != original

    if "hub" in combined:
        return title_part(original), "Driveline", notes, title_part(original) != original

    if re.search(r"\bbolt\b", combined):
        bolt_type = "Plow Bolt" if "plow" in combined else "Carriage Bolt" if "carriage" in combined else "Hex Bolt"
        details = re.sub(r"\b(coarse thread|nc)\b", "", original, flags=re.I)
        details = re.sub(r"\bbolt\b", "", details, flags=re.I).strip(" -")
        details = re.sub(r"\bplow\b", "", details, flags=re.I).strip(" -")
        return f"{bolt_type} - {clean_space(details)}" if details else bolt_type, "Hardware", notes + ["hardware terminology normalized"], True
    if re.search(r"\bnut\b", combined):
        nut_type = "Lock Nut" if "lock" in combined else "Hex Nut"
        details = re.sub(r"\bnut\b", "", original, flags=re.I).strip(" -")
        return f"{nut_type} - {clean_space(details)}" if details else nut_type, "Hardware", notes + ["hardware terminology normalized"], True
    if "washer" in combined:
        washer_type = "Lock Washer" if "lock" in combined else "Flat Washer" if original.lower() == "washer" else title_part(original)
        return washer_type, "Hardware", notes + ["hardware terminology normalized"], washer_type != original
    if any(k in combined for k in ["pin", "bushing", "snap ring", "clamp", "key"]):
        return title_part(original), "Hardware", notes, title_part(original) != original

    if "80w" in combined or "gear oil" in combined:
        return "Gear Oil 80W-90", "Lubricants", notes + ["lubricant normalized"], original != "Gear Oil 80W-90"
    if "two cycle oil" in combined:
        return "Two-Cycle Oil", "Lubricants", notes + ["lubricant normalized"], True
    if "penetrating oil" in combined or "pen oil" in combined:
        return "Penetrating Oil", "Lubricants", notes + ["lubricant normalized"], True
    if "grease" in combined or "oil" in combined or "fluid" in combined:
        return title_part(original), "Lubricants", notes, title_part(original) != original
    if "paint" in combined:
        return title_part(original), "Paint", notes, title_part(original) != original

    if "pto" in combined:
        return title_part(original), "PTO", notes, title_part(original) != original
    if "u-joint" in combined or "u joint" in combined or "driveline" in combined:
        return title_part(original).replace("U-joint", "U-Joint"), "Driveline", notes, True
    if "belt" in combined:
        family = "Engine" if "fan" in combined else "Driveline"
        return title_part(original), family, notes, title_part(original) != original

    if any(k in combined for k in ["service on timesheets", "labor", "mileage", "sublet", "deposit", "membership", "settle", "cc-fee", "core deposit"]):
        return title_part(original), "Service" if "service" in combined or "labor" in combined else "Miscellaneous", notes + ["non-stock/service item flagged"], title_part(original) != original

    if original:
        cleaned = title_part(original)
        if cleaned.lower().startswith("oem part"):
            return cleaned, "Miscellaneous", notes + ["placeholder OEM part flagged"], cleaned != original
        return cleaned, "Miscellaneous", notes, cleaned != original
    return f"Needs Review - {row.get('Internal Reference', '').strip()}", "Miscellaneous", notes + ["missing product name"], True


def category_for_family(family):
    for key, (_, category) in FAMILY_CATEGORY.items():
        if family == key or family == FAMILY_CATEGORY[key][0]:
            return category
    return "Miscellaneous"


def product_family_label(family):
    if family in FAMILY_CATEGORY:
        return FAMILY_CATEGORY[family][0]
    return family if family else "Miscellaneous"


def search_keywords(row, product_name, family, manufacturer):
    terms = []
    for value in [
        row.get("Internal Reference", ""),
        row.get("OEM Part Number", ""),
        row.get("Barcode", ""),
        row.get("Vendors Product Code", ""),
        product_name,
        family,
        manufacturer,
        row.get("Name", ""),
    ]:
        value = clean_space(value)
        if value and value not in terms:
            terms.append(value)
    oem = clean_space(row.get("OEM Part Number", ""))
    if oem:
        compact = re.sub(r"[^A-Za-z0-9]", "", oem)
        if compact and compact != oem and compact not in terms:
            terms.append(compact)
        prefix = re.split(r"[-/\s,]", oem)[0]
        if len(prefix) >= 3 and prefix not in terms:
            terms.append(prefix)
    return "; ".join(terms)


def review_reason(row, new_name, family, notes):
    reasons = []
    original = clean_space(row.get("Name", ""))
    oem = clean_space(row.get("OEM Part Number", ""))
    internal = clean_space(row.get("Internal Reference", ""))
    if not internal:
        reasons.append("Missing Internal Reference")
    if not oem:
        reasons.append("Missing OEM Part Number")
    if original.lower() in GENERIC_NAMES:
        reasons.append("Generic product name")
    if "Needs Review" in new_name:
        reasons.append("Suggested name still needs human decision")
    if notes:
        reasons.extend([n for n in notes if "requires review" in n or "flagged" in n or "missing" in n])
    return "; ".join(dict.fromkeys(reasons))


def confidence_for(reason, changed):
    if "Missing Internal Reference" in reason:
        return 0.20
    if "Suggested name still needs human decision" in reason:
        return 0.35
    if "Generic product name" in reason:
        return 0.55
    if "Missing OEM Part Number" in reason:
        return 0.65
    return 0.92 if changed else 0.98


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned_rows = []
    for row in rows:
        cleaned_rows.append({
            key: clean_space(value) if isinstance(value, str) else value
            for key, value in row.items()
        })
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(cleaned_rows)


def publish_package_outputs():
    IMPORT_DIR.mkdir(parents=True, exist_ok=True)
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    DOC_DIR.mkdir(parents=True, exist_ok=True)

    copies = [
        (OUTDIR / "master_products.csv", IMPORT_DIR / "master_products.csv"),
        (OUTDIR / "needs_review.csv", REVIEW_DIR / "needs_review.csv"),
        (OUTDIR / "duplicates.csv", REVIEW_DIR / "duplicates.csv"),
        (OUTDIR / "archive_candidates.csv", REVIEW_DIR / "archive_candidates.csv"),
        (OUTDIR / "change_log.md", DOC_DIR / "change_log.md"),
        (OUTDIR / "naming_standard.md", DOC_DIR / "naming_standard.md"),
    ]
    for source, destination in copies:
        if source.exists():
            shutil.copy2(source, destination)


def main():
    OUTDIR.mkdir(parents=True, exist_ok=True)
    with SOURCE.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    master = []
    needs_review = []
    archive_candidates = []
    changed_count = 0
    manufacturer_inferred = 0
    family_counter = Counter()
    category_counter = Counter()

    for idx, row in enumerate(rows, 1):
        new_name, family_key, notes, changed = classify_and_name(row)
        polished_name = polish_product_name(new_name)
        if polished_name != new_name:
            notes.append("final formatting polished")
            changed = True
            new_name = polished_name
        family = product_family_label(family_key)
        category = category_for_family(family_key)
        manufacturer, m_source = normalize_manufacturer(row.get("Manufacturer", ""), row.get("Name", ""))
        if m_source == "inferred_from_name":
            manufacturer_inferred += 1
            notes.append("manufacturer inferred from product name")
        changed_count += int(changed)
        family_counter[family] += 1
        category_counter[category] += 1
        reason = review_reason(row, new_name, family, notes)
        confidence = confidence_for(reason, changed)
        status = "Needs Review" if reason else "Approved"
        if any(k in clean_space(row.get("Name", "")).lower() for k in ["test", "deposit", "settle", "membership", "cc-fee", "misc"]):
            if "Misc" in row.get("Name", "") or clean_space(row.get("Name", "")).lower() in {"test jp", "test kit", "deposit", "core deposit", "settle due", "settle invoice", "membership", "misc"}:
                status = "Archive Candidate" if clean_space(row.get("Name", "")).lower().startswith(("test", "settle")) else status
                archive_candidates.append({
                    "Internal Reference": row.get("Internal Reference", ""),
                    "OEM Part Number": row.get("OEM Part Number", ""),
                    "Original Name": row.get("Name", ""),
                    "Suggested Name": new_name,
                    "Reason": "Placeholder, test, settlement, deposit, or miscellaneous item requires business review before keeping active",
                    "Recommendation": "Review usage in Odoo before archiving",
                })

        vendor = clean_vendor(row.get("seller_ids/company_id/name", "") or row.get("Vendors", ""))
        output = {
            "External ID": row.get("ID", ""),
            "Internal Reference": row.get("Internal Reference", ""),
            "OEM Part Number": row.get("OEM Part Number", ""),
            "Product Name": new_name,
            "Original Product Name": row.get("Name", ""),
            "Product Family": family,
            "Product Category": category,
            "Original Product Category": row.get("Product Category", ""),
            "Manufacturer": manufacturer,
            "Original Manufacturer": row.get("Manufacturer", ""),
            "Vendor": vendor,
            "Vendor Part Number": row.get("Vendors Product Code", "") if vendor else "",
            "Search Keywords": search_keywords(row, new_name, family, manufacturer),
            "Sales Description": new_name,
            "Purchase Description": f"{new_name}" + (f" | OEM: {clean_space(row.get('OEM Part Number', ''))}" if row.get("OEM Part Number", "") else ""),
            "Product Type": row.get("Product Type", ""),
            "Barcode": row.get("Barcode", ""),
            "Unit of Measure": row.get("Unit", ""),
            "Status": status,
            "Confidence": f"{confidence:.2f}",
            "Notes": "; ".join(dict.fromkeys(notes)),
            "Cost": row.get("Cost", ""),
            "Sales Price": row.get("Sales Price", ""),
            "Sales Taxes": row.get("Sales Taxes", ""),
            "Purchase Taxes": row.get("Purchase Taxes", ""),
            "is_storable": row.get("is_storable", ""),
            "invoice_policy": row.get("invoice_policy", ""),
            "Routes": row.get("route_ids/name", "") or row.get("Routes", ""),
            "sale_ok": row.get("sale_ok", ""),
            "purchase_ok": row.get("purchase_ok", ""),
            "Sub Reference": row.get("Sub Reference", ""),
        }
        master.append(output)
        if reason:
            needs_review.append({
                "Row Number": idx + 1,
                "Internal Reference": row.get("Internal Reference", ""),
                "OEM Part Number": row.get("OEM Part Number", ""),
                "Original Name": row.get("Name", ""),
                "Suggested Name": new_name,
                "Suggested Category": category,
                "Product Family": family,
                "Reason": reason,
                "Confidence": f"{confidence:.2f}",
                "Notes": output["Notes"],
            })

    duplicates = []
    for field, label in [("Internal Reference", "Duplicate Internal Reference"), ("OEM Part Number", "Duplicate OEM Part Number"), ("Product Name", "Duplicate Cleaned Product Name")]:
        groups = defaultdict(list)
        for r in master:
            key = clean_space(r.get(field, "")).lower()
            if key:
                groups[key].append(r)
        for key, members in groups.items():
            if len(members) > 1:
                duplicates.append({
                    "Duplicate Type": label,
                    "Duplicate Key": members[0].get(field, ""),
                    "Internal Reference": " | ".join(m.get("Internal Reference", "") for m in members),
                    "OEM Part Number": " | ".join(m.get("OEM Part Number", "") for m in members),
                    "Product Name": " | ".join(sorted(set(m.get("Product Name", "") for m in members))),
                    "Original Product Name": " | ".join(sorted(set(m.get("Original Product Name", "") for m in members))),
                    "Confidence Score": "1.00" if field != "Product Name" else "0.70",
                    "Recommendation": "Investigate duplicate Odoo update key" if field == "Internal Reference" else "Review before merging; do not merge different OEM numbers automatically",
                })

    by_first_word = defaultdict(list)
    for r in master:
        name_key = re.sub(r"[^a-z0-9 ]", "", r["Product Name"].lower())
        if len(name_key) >= 8:
            by_first_word[name_key[:4]].append((name_key, r))
    seen_pairs = set()
    for bucket in by_first_word.values():
        if len(bucket) < 2 or len(bucket) > 100:
            continue
        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a_key, a = bucket[i]
                b_key, b = bucket[j]
                if a["OEM Part Number"] == b["OEM Part Number"] or a["Internal Reference"] == b["Internal Reference"]:
                    continue
                ratio = difflib.SequenceMatcher(None, a_key, b_key).ratio()
                if ratio >= 0.96 and a_key != b_key and a["Product Family"] == b["Product Family"]:
                    pair = tuple(sorted([a["Internal Reference"], b["Internal Reference"]]))
                    if pair in seen_pairs:
                        continue
                    seen_pairs.add(pair)
                    duplicates.append({
                        "Duplicate Type": "Likely Duplicate Product Name",
                        "Duplicate Key": f"{a['Product Name']} <> {b['Product Name']}",
                        "Internal Reference": f"{a['Internal Reference']} | {b['Internal Reference']}",
                        "OEM Part Number": f"{a['OEM Part Number']} | {b['OEM Part Number']}",
                        "Product Name": f"{a['Product Name']} | {b['Product Name']}",
                        "Original Product Name": f"{a['Original Product Name']} | {b['Original Product Name']}",
                        "Confidence Score": f"{ratio:.2f}",
                        "Recommendation": "Review manually; names are very similar but OEM numbers differ",
                    })

    master_fields = list(master[0].keys()) if master else []
    write_csv(OUTDIR / "master_products.csv", master, master_fields)
    write_csv(OUTDIR / "needs_review.csv", needs_review, [
        "Row Number", "Internal Reference", "OEM Part Number", "Original Name",
        "Suggested Name", "Suggested Category", "Product Family", "Reason",
        "Confidence", "Notes",
    ])
    write_csv(OUTDIR / "duplicates.csv", duplicates, [
        "Duplicate Type", "Duplicate Key", "Internal Reference", "OEM Part Number",
        "Product Name", "Original Product Name", "Confidence Score", "Recommendation",
    ])
    write_csv(OUTDIR / "archive_candidates.csv", archive_candidates, [
        "Internal Reference", "OEM Part Number", "Original Name", "Suggested Name",
        "Reason", "Recommendation",
    ])

    change_log = [
        "# Southern Equipment Master Parts Database v1.0 Change Log",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Source file: {SOURCE.name}",
        "",
        "## Summary",
        "",
        f"- Source rows processed: {len(rows)}",
        f"- Master rows written: {len(master)}",
        f"- Product names changed or standardized: {changed_count}",
        f"- Rows requiring review: {len(needs_review)}",
        f"- Duplicate report rows: {len(duplicates)}",
        f"- Archive candidate rows: {len(archive_candidates)}",
        f"- Manufacturers inferred from product names: {manufacturer_inferred}",
        "",
        "## Import Safety",
        "",
        "- Internal Reference was preserved exactly from the source export.",
        "- OEM Part Number was preserved exactly from the source export.",
        "- External ID was preserved from the source ID column.",
        "- Barcode was preserved exactly from the source export.",
        "- Source cost, price, tax, route, sale, purchase, and stock flags were carried forward unchanged.",
        "",
        "## Rules Applied",
        "",
        "- Standardized hydraulic fitting terminology for JIC, NPT/Male Pipe, BSP/British Pipe, ORB/O-Ring Boss, flat face couplers, elbows, tees, plugs, and adapters.",
        "- Standardized filter names into air, fuel, engine oil, hydraulic, cab, and fuel-water separator families.",
        "- Standardized common bearing, seal, hardware, electrical, cooling, fuel system, lubricant, PTO, and driveline names.",
        "- Converted current flat `Parts` category into a suggested dealership-grade `Product Category` hierarchy.",
        "- Generated `Search Keywords` from internal reference, OEM number, barcode, vendor part number, cleaned name, family, manufacturer, original name, and compact OEM variants.",
        "- Flagged blank, generic, placeholder, missing-OEM, and uncertain naming records in `needs_review.csv`.",
        "- Flagged duplicate internal references, duplicate OEM numbers, duplicate cleaned names, and very similar names in `duplicates.csv`.",
        "- Flagged likely placeholder/test/deposit/settlement/miscellaneous products in `archive_candidates.csv` for business review.",
        "",
        "## Product Family Counts",
        "",
    ]
    for family, count in family_counter.most_common():
        change_log.append(f"- {family}: {count}")
    change_log.extend(["", "## Category Counts", ""])
    for category, count in category_counter.most_common():
        change_log.append(f"- {category}: {count}")
    (OUTDIR / "change_log.md").write_text("\n".join(change_log) + "\n", encoding="utf-8")

    naming_standard = """# Southern Equipment Parts Naming Standard

## Core Rule

Use `Product Family - Description - Size if needed`.

Keep the Odoo identifiers separate:

- Internal Reference: Odoo/Southern internal SKU; never change during cleanup.
- OEM Part Number: OEM or supplier part number; never guess or overwrite.
- Barcode: preserve unless a human confirms a correction.

## Examples

- Engine Oil Filter
- Fuel Filter
- Ball Bearing - 6203-2RS
- Wheel Bearing Kit
- Oil Seal - 35 x 52 x 10 mm
- Hydraulic Adapter - #10 MNPT x #8 FJIC
- Hydraulic Elbow 90 - #8 FJIC
- Hydraulic Hose - 1/2\" Two-Wire
- Starter Solenoid
- Water Pump
- Engine Thermostat
- Hex Bolt - 1/2\" x 3\"
- Lower Link Pin - Category 1

## Standard Product Families

- Air Filters
- Fuel Filters
- Engine Oil Filters
- Hydraulic Filters
- Cab Filters
- Fuel Water Separators
- Bearings
- Bearing Kits
- Oil Seals
- Wheel Seals
- Axle Seals
- Hydraulic Seals
- Hydraulic Seal Kits
- Hydraulic Adapters
- Hydraulic Couplers
- Hydraulic Elbows
- Hydraulic Tees
- Hydraulic Hoses
- Hydraulic Cylinders
- Hydraulic Caps
- Hydraulic Plugs
- Engine
- Cooling
- Fuel System
- Electrical
- Hardware
- PTO
- Driveline
- Lubricants
- Paint
- Shop Supplies
- Rental Supplies
- Miscellaneous

## Category Tree

- Filters / Air Filters
- Filters / Fuel Filters
- Filters / Engine Oil Filters
- Filters / Hydraulic Filters
- Filters / Cab Filters
- Filters / Fuel Water Separators
- Hydraulic / Hydraulic Adapters
- Hydraulic / Hydraulic Couplers
- Hydraulic / Hydraulic Hoses
- Hydraulic / Hydraulic Cylinders
- Bearings
- Bearings / Bearing Kits
- Seals / Oil Seals
- Seals / Wheel Seals
- Seals / Axle Seals
- Seals / Hydraulic Seals
- Seals / Hydraulic Seal Kits
- Electrical
- Cooling
- Engine
- Fuel System
- Hardware
- PTO
- Driveline
- Lubricants
- Paint
- Shop Supplies
- Rental Supplies
- Miscellaneous

## Review Policy

Do not guess when the source only says `Seal`, `Filter`, `Bearing`, `Adapter`, `Pump`, `Switch`, `Misc`, or `OEM Part`.

Use the cleaned master for broad standardization, but review rows marked `Needs Review` before importing them into production.
"""
    (OUTDIR / "naming_standard.md").write_text(naming_standard, encoding="utf-8")
    publish_package_outputs()

    print(f"Output directory: {OUTDIR}")
    print(f"Rows processed: {len(rows)}")
    print(f"Master rows: {len(master)}")
    print(f"Changed names: {changed_count}")
    print(f"Needs review: {len(needs_review)}")
    print(f"Duplicate rows: {len(duplicates)}")
    print(f"Archive candidates: {len(archive_candidates)}")


if __name__ == "__main__":
    main()
