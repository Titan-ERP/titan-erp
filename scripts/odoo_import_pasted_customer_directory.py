import argparse
import csv
import html
import os
import re
import sys
import xmlrpc.client
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
DEFAULT_SOURCE = ROOT / "inputs" / "pasted_customer_directory.txt"
CRM_DIR = ROOT / "odoo_imports" / "crm"
OUT = CRM_DIR / "pasted_customer_directory_import_results.csv"
SUMMARY = CRM_DIR / "pasted_customer_directory_import_summary.md"
MARKER = "Pasted customer directory import"

COMPANY_WORDS = {
    "AG",
    "AGENCY",
    "AUTO",
    "BANK",
    "CO",
    "COMPANY",
    "CONCRETE",
    "CONSTRUCTION",
    "CONTRACTING",
    "CONTRACTORS",
    "CORP",
    "COUNTY",
    "DBA",
    "EQUIPMENT",
    "FARM",
    "FARMS",
    "HARDWARE",
    "INC",
    "INDUSTRIAL",
    "LLC",
    "LTD",
    "MACHINERY",
    "PARTS",
    "RENTALS",
    "ROADBUILDERS",
    "SALES",
    "SERVICE",
    "SERVICES",
    "SUPPLY",
    "TRACTOR",
    "TRUCKING",
    "WELDING",
}


def load_env(path):
    if not path.exists():
        raise SystemExit(f"Missing {path}. Copy odoo_connection.env.example and fill it in.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    load_env(ENV_PATH)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def partner_fields(models, db, uid, api_key):
    return execute(models, db, uid, api_key, "res.partner", "fields_get", [], {"attributes": ["string", "type"]})


def norm(value):
    raw = str(value or "").upper().replace("&", " AND ").strip()
    raw = re.sub(r"\s+-\s+V$", "", raw)
    raw = raw.replace("'", "")
    text = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def is_vendor_name(raw):
    return bool(re.search(r"\s+-\s+V\s*$", raw or "", re.IGNORECASE))


def smart_title(value):
    special = {
        "A": "A",
        "AG": "AG",
        "AMS": "AMS",
        "AP": "AP",
        "BMS": "BMS",
        "CO": "Co",
        "DBA": "DBA",
        "HD": "HD",
        "HWY": "Hwy",
        "IMSA": "IMSA",
        "INC": "Inc",
        "JD": "JD",
        "JLG": "JLG",
        "LLC": "LLC",
        "MDR": "MDR",
        "MS": "MS",
        "NAPA": "NAPA",
        "PO": "PO",
        "PTO": "PTO",
        "RO": "RO",
        "USA": "USA",
        "USPS": "USPS",
        "WPI": "WPI",
    }
    words = []
    for word in re.split(r"(\s+)", str(value).strip()):
        if not word or word.isspace():
            words.append(word)
            continue
        clean = re.sub(r"[^A-Za-z0-9]", "", word).upper()
        if clean in special:
            words.append(word.upper().replace(clean, special[clean]))
        else:
            words.append(word[:1].upper() + word[1:].lower())
    return "".join(words).strip()


def should_flip_person(left, right):
    left_words = set(norm(left).split())
    right_words = set(norm(right).split())
    if left_words & COMPANY_WORDS:
        return False
    if right_words & COMPANY_WORDS:
        return False
    return 1 <= len(right_words) <= 3


def display_name(raw_name):
    raw = re.sub(r"\s+-\s+V\s*$", "", str(raw_name or "").strip(), flags=re.IGNORECASE).strip()
    raw = re.sub(r"\s+", " ", raw)
    if not raw:
        return ""
    if raw.startswith(","):
        raw = raw.strip(", ").strip()
        return smart_title(raw)
    if "," in raw:
        left, right = [part.strip() for part in raw.split(",", 1)]
        if left and right and should_flip_person(left, right):
            return smart_title(f"{right} {left}")
    return smart_title(raw)


def clean_phone(value):
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return str(value or "").strip()


def empty_profile():
    return {
        "raw_names": set(),
        "display_name": "",
        "addresses": set(),
        "phones": set(),
        "quotes": [],
        "active_yes": False,
        "is_vendor": False,
        "line_numbers": [],
    }


def parse_source(path):
    profiles = defaultdict(empty_profile)
    last_key = None
    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if not line.strip():
            continue
        if line.startswith("Open Quote #"):
            if last_key:
                profiles[last_key]["quotes"].append(line.strip())
            continue
        parts = line.split("\t")
        if len(parts) < 6:
            parts = parts + [""] * (6 - len(parts))
        name, address, phone_a, phone_b, phone_c, active = [part.strip() for part in parts[:6]]
        if not name or name.upper() == "PO BOX 246":
            continue
        key = norm(name)
        if not key:
            continue
        profile = profiles[key]
        profile["raw_names"].add(name)
        profile["display_name"] = profile["display_name"] or display_name(name)
        if address:
            profile["addresses"].add(address)
        for value in [phone_a, phone_b, phone_c]:
            phone = clean_phone(value)
            if phone:
                profile["phones"].add(phone)
        profile["active_yes"] = profile["active_yes"] or active.upper() == "YES"
        profile["is_vendor"] = profile["is_vendor"] or is_vendor_name(name)
        profile["line_numbers"].append(line_number)
        last_key = key
    return profiles


def contact_note(profile):
    raw_names = "; ".join(sorted(profile["raw_names"]))
    addresses = sorted(profile["addresses"])
    phones = sorted(profile["phones"])
    quotes = profile["quotes"]
    address_items = "".join(f"<li>{html.escape(item)}</li>" for item in addresses) or "<li>No address in source row.</li>"
    phone_items = "".join(f"<li>{html.escape(item)}</li>" for item in phones) or "<li>No phone in source row.</li>"
    quote_items = "".join(f"<li>{html.escape(item)}</li>" for item in quotes) or "<li>No open quote continuation in source.</li>"
    return (
        f"<p><strong>{MARKER}</strong></p>"
        "<ul>"
        f"<li>Source names: {html.escape(raw_names)}</li>"
        f"<li>Source line numbers: {html.escape(', '.join(map(str, profile['line_numbers'])))}</li>"
        f"<li>Active flag: {'YES' if profile['active_yes'] else ''}</li>"
        f"<li>Vendor marker (- V): {'yes' if profile['is_vendor'] else 'no'}</li>"
        "</ul>"
        "<p><strong>Source addresses</strong></p><ul>"
        f"{address_items}"
        "</ul><p><strong>Source phones</strong></p><ul>"
        f"{phone_items}"
        "</ul><p><strong>Source quote notes</strong></p><ul>"
        f"{quote_items}"
        "</ul>"
    )


def is_company(profile):
    name = norm(profile["display_name"])
    words = set(name.split())
    if profile["is_vendor"]:
        return True
    if words & COMPANY_WORDS:
        return True
    raw = next(iter(profile["raw_names"]), "")
    return raw.upper() == raw and "," not in raw


def load_existing_partners(models, db, uid, api_key):
    fields = ["id", "name", "street", "phone", "ref", "comment", "customer_rank", "supplier_rank"]
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "search_read",
        [[]],
        {"fields": fields, "limit": 0, "context": {"active_test": False}},
    )
    by_norm = defaultdict(list)
    for row in rows:
        by_norm[norm(row.get("name"))].append(row)
    return by_norm


def choose_existing(matches):
    if not matches:
        return None, "no match"
    if len(matches) == 1:
        return matches[0], "normalized name"
    contactable = [row for row in matches if row.get("phone") or row.get("street")]
    if len(contactable) == 1:
        return contactable[0], "contactable duplicate normalized name"
    with_note = [row for row in matches if MARKER in str(row.get("comment") or "")]
    if len(with_note) == 1:
        return with_note[0], "previous directory import marker"
    return None, "multiple normalized name matches"


def create_values(profile, field_map):
    values = {"name": profile["display_name"]}
    if "customer_rank" in field_map:
        values["customer_rank"] = 1
    if profile["is_vendor"] and "supplier_rank" in field_map:
        values["supplier_rank"] = 1
    if "is_company" in field_map:
        values["is_company"] = is_company(profile)
    if profile["addresses"] and "street" in field_map:
        values["street"] = sorted(profile["addresses"])[0]
    if profile["phones"] and "phone" in field_map:
        values["phone"] = sorted(profile["phones"])[0]
    if "ref" in field_map:
        values["ref"] = f"Directory: {next(iter(sorted(profile['raw_names'])))}"
    if "comment" in field_map:
        values["comment"] = contact_note(profile)
    return values


def update_values(profile, partner, field_map):
    values = {}
    if "customer_rank" in field_map and int(partner.get("customer_rank") or 0) < 1:
        values["customer_rank"] = 1
    if profile["is_vendor"] and "supplier_rank" in field_map and int(partner.get("supplier_rank") or 0) < 1:
        values["supplier_rank"] = 1
    if profile["addresses"] and "street" in field_map and not partner.get("street"):
        values["street"] = sorted(profile["addresses"])[0]
    if profile["phones"] and "phone" in field_map and not partner.get("phone"):
        values["phone"] = sorted(profile["phones"])[0]
    if "ref" in field_map and not partner.get("ref"):
        values["ref"] = f"Directory: {next(iter(sorted(profile['raw_names'])))}"
    if "comment" in field_map and MARKER not in str(partner.get("comment") or ""):
        existing = str(partner.get("comment") or "")
        values["comment"] = existing + contact_note(profile) if existing else contact_note(profile)
    return values


def write_results(rows):
    CRM_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "Status",
        "Reason",
        "Source Names",
        "Odoo Name",
        "Address Count",
        "Phone Count",
        "Quote Count",
        "Vendor Marker",
        "Odoo Partner ID",
        "Odoo Partner",
        "Match Method",
        "Updated Fields",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows, source_path, apply):
    counts = defaultdict(int)
    for row in rows:
        counts[row["Status"]] += 1
    SUMMARY.write_text(
        f"""# Pasted Customer Directory Import Summary

Source: `{source_path}`

- Odoo write performed: {'yes' if apply else 'no, dry run only'}
- Created: {counts['Created']}
- Updated existing: {counts['Updated']}
- Matched existing: {counts['Matched']}
- Ready create: {counts['Ready Create']}
- Ready update: {counts['Ready Update']}
- Review: {counts['Review']}
- Skipped: {counts['Skipped']}

Results: `odoo_imports/crm/pasted_customer_directory_import_results.csv`
""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Import a pasted Shop Boss customer directory into Odoo contacts.")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="Path to the pasted text directory export.")
    parser.add_argument("--apply", action="store_true", help="Create/update Odoo contacts. Default is dry run.")
    args = parser.parse_args()

    source_path = Path(args.source)
    if not source_path.exists():
        raise SystemExit(f"Missing source file: {source_path}")

    db, uid, api_key, models = connect()
    field_map = partner_fields(models, db, uid, api_key)
    existing = load_existing_partners(models, db, uid, api_key)
    profiles = parse_source(source_path)
    results = []

    for key, profile in sorted(profiles.items(), key=lambda item: item[1]["display_name"]):
        if not profile["display_name"]:
            results.append({"Status": "Skipped", "Reason": "Missing contact name", "Source Names": "; ".join(sorted(profile["raw_names"]))})
            continue
        partner, match_method = choose_existing(existing.get(norm(profile["display_name"]), []))
        base = {
            "Source Names": "; ".join(sorted(profile["raw_names"])),
            "Odoo Name": profile["display_name"],
            "Address Count": len(profile["addresses"]),
            "Phone Count": len(profile["phones"]),
            "Quote Count": len(profile["quotes"]),
            "Vendor Marker": "yes" if profile["is_vendor"] else "no",
            "Match Method": match_method,
        }
        if match_method.startswith("multiple"):
            results.append({**base, "Status": "Review", "Reason": match_method})
            continue
        if partner:
            values = update_values(profile, partner, field_map)
            if values and args.apply:
                execute(models, db, uid, api_key, "res.partner", "write", [[partner["id"]], values])
                partner.update(values)
                status = "Updated"
                reason = "Matched existing contact and filled missing directory fields"
            elif values:
                status = "Ready Update"
                reason = "Matched existing contact with missing directory fields"
            else:
                status = "Matched"
                reason = "Existing Odoo contact already has directory data"
            results.append(
                {
                    **base,
                    "Status": status,
                    "Reason": reason,
                    "Odoo Partner ID": partner["id"],
                    "Odoo Partner": partner["name"],
                    "Updated Fields": "; ".join(sorted(values)),
                }
            )
            continue

        if args.apply:
            partner_id = execute(models, db, uid, api_key, "res.partner", "create", [create_values(profile, field_map)])
            partner = execute(models, db, uid, api_key, "res.partner", "read", [[partner_id]], {"fields": ["id", "name"]})[0]
            existing[norm(profile["display_name"])].append(partner)
            status = "Created"
            reason = "Created new directory contact"
        else:
            partner = {"id": "", "name": ""}
            status = "Ready Create"
            reason = "No existing Odoo contact matched"
        results.append(
            {
                **base,
                "Status": status,
                "Reason": reason,
                "Odoo Partner ID": partner["id"],
                "Odoo Partner": partner["name"],
            }
        )

    write_results(results)
    write_summary(results, source_path, args.apply)
    counts = defaultdict(int)
    for row in results:
        counts[row["Status"]] += 1
    print(f"Connected uid: {uid}")
    print(f"Source profiles: {len(profiles)}")
    print(f"Odoo write performed: {'yes' if args.apply else 'no, dry run only'}")
    print(f"Created: {counts['Created']}")
    print(f"Updated existing: {counts['Updated']}")
    print(f"Matched existing: {counts['Matched']}")
    print(f"Ready create: {counts['Ready Create']}")
    print(f"Ready update: {counts['Ready Update']}")
    print(f"Review: {counts['Review']}")
    print(f"Skipped: {counts['Skipped']}")
    print(f"Results: {OUT}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
