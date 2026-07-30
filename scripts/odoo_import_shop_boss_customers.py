import argparse
import csv
import html
import json
import os
import re
import sys
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_BOSS_DIR = ROOT / "odoo_imports" / "shop_boss"
CRM_DIR = ROOT / "odoo_imports" / "crm"
INVOICE_ROWS = SHOP_BOSS_DIR / "shop_boss_all_invoice_rows_finalized_closed_2026_07.csv"
PAYMENTS_ROWS = SHOP_BOSS_DIR / "shop_boss_payments_received_2026_07.csv"
AR_OPEN_ROWS = SHOP_BOSS_DIR / "shop_boss_ar_open_2026_07.csv"
WIP_ROWS = SHOP_BOSS_DIR / "shop_boss_wip_snapshot_2026_07_25.csv"
DETAILS_JSON = SHOP_BOSS_DIR / "shop_boss_part_sale_details_2026_07.json"
OUT = CRM_DIR / "shop_boss_customer_import_results_2026_07.csv"
SUMMARY = CRM_DIR / "shop_boss_customer_import_summary_2026_07.md"

SKIP_NORMALIZED = {
    "WALK IN",
    "WALK INS",
}
NAME_OVERRIDES = {
    "BEN BOHANEN": "Ben Bohannon",
    "CITY OF LAUREL": "City of Laurel",
    "CASH": "cash",
    "DICKERSON AND BOWEN": "Dickerson & Bowen",
    "DUNN ROADBUILDERS": "Dunn Roadbuilders",
    "EQUIPMENT SOUTHERN": "Equipment Southern",
    "FREEMAN ZACH": "Zach Freeman",
    "GERALD HENDERSON": "Gerald Henderson",
    "GRAVES TIM": "Tim Graves",
    "GRIFFIN GREG": "Greg Griffin",
    "H AND B SERVICES": "H & B Services",
    "HD IRON LLC": "HD Iron, LLC",
    "IMSA INTERNATIONAL MANAGEMENT STAFFING AGENCY": "IMSA- International Management Staffing Agency",
    "J W CHAIN CONTRACTORS": "J W Chain Contractors",
    "JIMMY BADGET": "Jimmy Badgett",
    "MDR CONSTRUCTION": "MDR Construction",
    "MICHAEL MCORMICK": "Michael McCormick",
    "MOSELLE RECYCLING": "Moselle Recycling",
    "ORLANDO JOHNIKIN": "Orlando Johnikin",
    "PALMER ROBERT": "Robert Palmer",
    "ROBERT PLAMER": "Robert Palmer",
    "ROSS MANNS ROSS CATTLE CO": "Ross Manns / Ross Cattle Co.",
    "SOUTHERN EQUIPMENT AND PARTS": "Southern Equipment & Parts",
    "TAYLOR CONSTRUCTION": "Taylor Construction",
    "WILLIAM SPRINGER": "William Springer",
}
CASH_NORMALIZED = {"CASH", "CASH WALKINS", "WALKINS CASH"}
COMPANY_WORDS = {
    "AGENCY",
    "BROTHERS",
    "CONSTRUCTION",
    "CONTRACTORS",
    "COUNTY",
    "DRILLING",
    "HOSPITAL",
    "INC",
    "LLC",
    "MANAGEMENT",
    "MDR",
    "OUTDOORS",
    "RECYCLING",
    "ROADBUILDERS",
    "SERVICES",
    "STAFFING",
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


def fields(models, db, uid, api_key, model):
    return execute(models, db, uid, api_key, model, "fields_get", [], {"attributes": ["string", "type"]})


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(value):
    value = money(value)
    return f"${value:,.2f}"


def norm(value):
    raw = str(value or "").upper().replace("&", " AND ").strip()
    if "," in raw:
        left, right = [part.strip() for part in raw.split(",", 1)]
        company_suffixes = {"LLC", "INC", "LTD", "CO", "CO.", "CORP", "CORPORATION", "LP", "LLP"}
        if left and right and right not in company_suffixes:
            raw = f"{right} {left}"
    text = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def title_name(normalized):
    if normalized in NAME_OVERRIDES:
        return NAME_OVERRIDES[normalized]
    return " ".join(part.title() for part in normalized.split())


def clean_phone(value):
    digits = re.sub(r"\D+", "", str(value or ""))
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def display_phone(digits):
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return digits


def parse_contact_blob(value):
    text = str(value or "").strip()
    email_match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
    email = email_match.group(0).lower() if email_match else ""
    without_email = text.replace(email_match.group(0), "") if email_match else text
    phone = display_phone(clean_phone(without_email))
    return phone, email


def detail_contacts():
    if not DETAILS_JSON.exists():
        return {}
    data = json.loads(DETAILS_JSON.read_text(encoding="utf-8"))
    contacts = {}
    for record in data:
        customer = ""
        phone = ""
        email = ""
        for row in record.get("rows", []):
            if row and row[0] == "Customer:" and len(row) > 1:
                customer = row[1]
            if row and row[0] == "Home/Work:":
                blobs = []
                if len(row) > 1:
                    blobs.append(row[1])
                if len(row) > 3:
                    blobs.append(row[3])
                for blob in blobs:
                    parsed_phone, parsed_email = parse_contact_blob(blob)
                    phone = phone or parsed_phone
                    email = email or parsed_email
        if customer:
            contacts[norm(customer)] = {"phone": phone, "email": email}
    return contacts


def empty_profile():
    return {
        "invoice_count": 0,
        "invoice_total": Decimal("0.00"),
        "payment_count": 0,
        "payment_total": Decimal("0.00"),
        "open_ar_count": 0,
        "open_ar_total": Decimal("0.00"),
        "open_ar_balance": Decimal("0.00"),
        "wip_count": 0,
        "wip_total": Decimal("0.00"),
        "first_date": "",
        "last_date": "",
        "raw_names": set(),
        "phones": set(),
        "emails": set(),
        "pos": set(),
        "vehicles": set(),
        "payment_types": set(),
        "invoice_refs": [],
        "payment_refs": [],
        "ar_refs": [],
        "wip_refs": [],
    }


def add_date(profile, value):
    value = str(value or "").strip()
    if not value:
        return
    dates = [d for d in [profile["first_date"], value] if d]
    profile["first_date"] = min(dates) if dates else value
    profile["last_date"] = max(dates) if dates else value


def mmddyyyy_to_iso(value):
    text = str(value or "").strip()
    if not text or "/" not in text:
        return text
    month, day, year = text.split("/")
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def profile_for(grouped, customer_name):
    key = norm(customer_name)
    if key in CASH_NORMALIZED:
        key = "CASH"
    profile = grouped[key]
    profile["raw_names"].add(str(customer_name or "").strip())
    return key, profile


def source_customers():
    details = detail_contacts()
    grouped = defaultdict(empty_profile)
    if INVOICE_ROWS.exists():
        for row in read_csv(INVOICE_ROWS):
            key, profile = profile_for(grouped, row["Shop Boss Customer"])
            profile["invoice_count"] += 1
            profile["invoice_total"] += money(row["Total"])
            add_date(profile, row["Shop Boss Date ISO"])
            profile["invoice_refs"].append(f"{row['Shop Boss Type']} {row['Shop Boss Number']} {row['Shop Boss Date ISO']} {format_money(row['Total'])}")
    if PAYMENTS_ROWS.exists():
        for row in read_csv(PAYMENTS_ROWS):
            key, profile = profile_for(grouped, row["customer"])
            profile["payment_count"] += 1
            profile["payment_total"] += money(row["amount"])
            add_date(profile, mmddyyyy_to_iso(row["payment_date"]))
            if row.get("payment_type"):
                profile["payment_types"].add(row["payment_type"])
            if row.get("vehicle"):
                profile["vehicles"].add(row["vehicle"])
            profile["payment_refs"].append(f"{row['type']} {row['number']} paid {mmddyyyy_to_iso(row['payment_date'])} {format_money(row['amount'])} via {row.get('payment_type') or 'unknown'}")
    if AR_OPEN_ROWS.exists():
        for row in read_csv(AR_OPEN_ROWS):
            key, profile = profile_for(grouped, row["customer"])
            profile["open_ar_count"] += 1
            profile["open_ar_total"] += money(row["total"])
            profile["open_ar_balance"] += money(row["balance"])
            add_date(profile, mmddyyyy_to_iso(row["date"]))
            profile["ar_refs"].append(f"{row['type']} {row['number']} dated {mmddyyyy_to_iso(row['date'])}; balance {format_money(row['balance'])}")
    if WIP_ROWS.exists():
        for row in read_csv(WIP_ROWS):
            key, profile = profile_for(grouped, row["WIP Customer"])
            if key == "CASH":
                continue
            profile["wip_count"] += 1
            profile["wip_total"] += money(row["WIP Total"])
            add_date(profile, mmddyyyy_to_iso(row["WIP Date"]))
            if row.get("WIP Vehicle"):
                profile["vehicles"].add(row["WIP Vehicle"])
            profile["wip_refs"].append(f"{row['WIP Type']} {row['WIP Number']} {row['WIP Status']} {mmddyyyy_to_iso(row['WIP Date'])} {format_money(row['WIP Total'])} {row.get('WIP Vehicle') or ''}".strip())

    customers = []
    for key, values in grouped.items():
        contact = details.get(key, {})
        if contact.get("phone"):
            values["phones"].add(contact["phone"])
        if contact.get("email"):
            values["emails"].add(contact["email"])
        customers.append(
            {
                "normalized": key,
                "shop_boss_name": sorted(values["raw_names"])[0],
                "odoo_name": title_name(key),
                "phone": sorted(values["phones"])[0] if values["phones"] else "",
                "email": sorted(values["emails"])[0] if values["emails"] else "",
                "invoice_count": values["invoice_count"],
                "invoice_total": values["invoice_total"],
                "payment_count": values["payment_count"],
                "payment_total": values["payment_total"],
                "open_ar_count": values["open_ar_count"],
                "open_ar_total": values["open_ar_total"],
                "open_ar_balance": values["open_ar_balance"],
                "wip_count": values["wip_count"],
                "wip_total": values["wip_total"],
                "first_date": values["first_date"],
                "last_date": values["last_date"],
                "vehicles": sorted(values["vehicles"]),
                "payment_types": sorted(values["payment_types"]),
                "invoice_refs": values["invoice_refs"],
                "payment_refs": values["payment_refs"],
                "ar_refs": values["ar_refs"],
                "wip_refs": values["wip_refs"],
            }
        )
    return sorted(customers, key=lambda item: item["odoo_name"])


def partner_normalized(row):
    return norm(row.get("name", ""))


def search_partner(models, db, uid, api_key, customer, partner_fields):
    fields_list = [
        field
        for field in ["id", "name", "email", "phone", "mobile", "customer_rank", "is_company", "ref", "comment"]
        if field == "id" or field in partner_fields
    ]
    if customer["email"]:
        rows = execute(models, db, uid, api_key, "res.partner", "search_read", [[("email", "=", customer["email"])]], {"fields": fields_list, "limit": 5})
        if len(rows) == 1:
            return rows[0], "email"
        if len(rows) > 1:
            return None, "multiple email matches"

    phone_digits = clean_phone(customer["phone"])
    if phone_digits:
        phone_domain = [("phone", "ilike", phone_digits[-7:])]
        if "mobile" in partner_fields:
            phone_domain = ["|", ("phone", "ilike", phone_digits[-7:]), ("mobile", "ilike", phone_digits[-7:])]
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "res.partner",
            "search_read",
            [phone_domain],
            {"fields": fields_list, "limit": 10},
        )
        phone_matches = [row for row in rows if phone_digits in clean_phone(row.get("phone")) or phone_digits in clean_phone(row.get("mobile"))]
        if len(phone_matches) == 1:
            return phone_matches[0], "phone"
        if len(phone_matches) > 1:
            return None, "multiple phone matches"

    target_norm = norm(customer["odoo_name"])
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "search_read",
        [[("name", "ilike", customer["odoo_name"])]],
        {"fields": fields_list, "limit": 20},
    )
    exact = [row for row in rows if partner_normalized(row) == target_norm]
    if len(exact) == 1:
        return exact[0], "normalized name"
    if len(exact) > 1:
        contactable = [row for row in exact if row.get("email") or row.get("phone")]
        if len(contactable) == 1:
            return contactable[0], "contactable duplicate normalized name"
        richer = [row for row in exact if row.get("comment")]
        if len(richer) == 1 and not contactable:
            return richer[0], "richer duplicate normalized name"
        return None, "multiple normalized name matches"
    return None, "no match"


def is_company(customer):
    words = set(customer["normalized"].split())
    if words & COMPANY_WORDS:
        return True
    return customer["shop_boss_name"].upper() == customer["shop_boss_name"] and "," not in customer["shop_boss_name"]


def partner_values(customer, partner_fields):
    values = {"name": customer["odoo_name"]}
    if "customer_rank" in partner_fields:
        values["customer_rank"] = 1
    if "is_company" in partner_fields:
        values["is_company"] = is_company(customer)
    if customer["email"] and "email" in partner_fields:
        values["email"] = customer["email"]
    if customer["phone"] and "phone" in partner_fields:
        values["phone"] = customer["phone"]
    if "ref" in partner_fields:
        values["ref"] = f"Shop Boss: {customer['shop_boss_name']}"
    if "comment" in partner_fields:
        values["comment"] = shop_boss_note(customer)
    return values


def shop_boss_note(customer):
    def li(items, limit=12):
        if not items:
            return "<li>None in exported Shop Boss data.</li>"
        shown = items[:limit]
        extra = len(items) - len(shown)
        lines = [f"<li>{html.escape(str(item))}</li>" for item in shown]
        if extra:
            lines.append(f"<li>{extra} additional records in source export.</li>")
        return "".join(lines)

    vehicles = "; ".join(customer["vehicles"][:12]) or "None in exported Shop Boss data"
    payment_types = "; ".join(customer["payment_types"]) or "None in exported Shop Boss data"
    return (
        "<p><strong>Shop Boss customer data import - July 2026</strong></p>"
        "<ul>"
        f"<li>Source names: {html.escape(customer['shop_boss_name'])}</li>"
        f"<li>Activity date range: {html.escape(customer['first_date'] or '')} to {html.escape(customer['last_date'] or '')}</li>"
        f"<li>Closed invoice rows: {customer['invoice_count']}; invoice total: {format_money(customer['invoice_total'])}</li>"
        f"<li>Payment rows: {customer['payment_count']}; payment total: {format_money(customer['payment_total'])}</li>"
        f"<li>Open AR rows: {customer['open_ar_count']}; open balance: {format_money(customer['open_ar_balance'])}</li>"
        f"<li>WIP rows: {customer['wip_count']}; WIP total: {format_money(customer['wip_total'])}</li>"
        f"<li>Payment types: {html.escape(payment_types)}</li>"
        f"<li>Vehicles/equipment: {html.escape(vehicles)}</li>"
        "</ul>"
        "<p><strong>Invoice records</strong></p><ul>"
        f"{li(customer['invoice_refs'])}"
        "</ul><p><strong>Payment records</strong></p><ul>"
        f"{li(customer['payment_refs'])}"
        "</ul><p><strong>Open AR records</strong></p><ul>"
        f"{li(customer['ar_refs'])}"
        "</ul><p><strong>WIP records</strong></p><ul>"
        f"{li(customer['wip_refs'])}"
        "</ul>"
    )


def missing_updates(customer, partner, partner_fields):
    updates = {}
    if "customer_rank" in partner_fields and not int(partner.get("customer_rank") or 0):
        updates["customer_rank"] = 1
    if customer["email"] and "email" in partner_fields and not partner.get("email"):
        updates["email"] = customer["email"]
    if customer["phone"] and "phone" in partner_fields and not partner.get("phone") and not partner.get("mobile"):
        updates["phone"] = customer["phone"]
    if "ref" in partner_fields and not partner.get("ref"):
        updates["ref"] = f"Shop Boss: {customer['shop_boss_name']}"
    if "comment" in partner_fields:
        existing = str(partner.get("comment") or "")
        marker = "Shop Boss customer data import - July 2026"
        if marker not in existing:
            updates["comment"] = (existing + shop_boss_note(customer)) if existing else shop_boss_note(customer)
    return updates


def write_results(rows):
    CRM_DIR.mkdir(parents=True, exist_ok=True)
    fields_list = [
        "Status",
        "Reason",
        "Shop Boss Customer",
        "Odoo Name",
        "Email",
        "Phone",
        "Invoice Count",
        "Invoice Total",
        "Payment Count",
        "Payment Total",
        "Open AR Count",
        "Open AR Balance",
        "WIP Count",
        "WIP Total",
        "First Date",
        "Last Date",
        "Odoo Partner ID",
        "Odoo Partner",
        "Match Method",
        "Updated Fields",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields_list, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows, apply):
    counts = defaultdict(int)
    for row in rows:
        counts[row["Status"]] += 1
    SUMMARY.write_text(
        f"""# Shop Boss Customer Import Summary

Sources:

- `odoo_imports/shop_boss/shop_boss_all_invoice_rows_finalized_closed_2026_07.csv`
- `odoo_imports/shop_boss/shop_boss_payments_received_2026_07.csv`
- `odoo_imports/shop_boss/shop_boss_ar_open_2026_07.csv`
- `odoo_imports/shop_boss/shop_boss_wip_snapshot_2026_07_25.csv`
- `odoo_imports/shop_boss/shop_boss_part_sale_details_2026_07.json`

- Odoo write performed: {'yes' if apply else 'no, dry run only'}
- Created: {counts['Created']}
- Matched existing: {counts['Matched']}
- Updated existing: {counts['Updated']}
- Ready create: {counts['Ready Create']}
- Ready update: {counts['Ready Update']}
- Skipped: {counts['Skipped']}
- Needs review: {counts['Review']}

Results: `odoo_imports/crm/shop_boss_customer_import_results_2026_07.csv`
""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Import Shop Boss customers into Odoo contacts.")
    parser.add_argument("--apply", action="store_true", help="Create/update safe Odoo customer contacts. Default is dry run.")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    partner_fields = fields(models, db, uid, api_key, "res.partner")
    rows = []

    for customer in source_customers():
        base = {
            "Shop Boss Customer": customer["shop_boss_name"],
            "Odoo Name": customer["odoo_name"],
            "Email": customer["email"],
            "Phone": customer["phone"],
            "Invoice Count": customer["invoice_count"],
            "Invoice Total": format_money(customer["invoice_total"]),
            "Payment Count": customer["payment_count"],
            "Payment Total": format_money(customer["payment_total"]),
            "Open AR Count": customer["open_ar_count"],
            "Open AR Balance": format_money(customer["open_ar_balance"]),
            "WIP Count": customer["wip_count"],
            "WIP Total": format_money(customer["wip_total"]),
            "First Date": customer["first_date"],
            "Last Date": customer["last_date"],
        }
        if customer["normalized"] in SKIP_NORMALIZED:
            reason = "Internal Southern Equipment company record"
            if customer["normalized"] in {"WALK IN", "WALK INS"}:
                reason = "Generic walk-in customer"
            rows.append({**base, "Status": "Skipped", "Reason": reason})
            continue

        partner, match_method = search_partner(models, db, uid, api_key, customer, partner_fields)
        if match_method.startswith("multiple"):
            rows.append({**base, "Status": "Review", "Reason": match_method})
            continue

        if partner:
            updates = missing_updates(customer, partner, partner_fields)
            if updates and args.apply:
                execute(models, db, uid, api_key, "res.partner", "write", [[partner["id"]], updates])
                status = "Updated"
                reason = "Matched existing contact and filled missing safe fields"
            elif updates:
                status = "Ready Update"
                reason = "Matched existing contact with missing safe fields"
            else:
                status = "Matched"
                reason = "Existing Odoo contact matched"
            rows.append(
                {
                    **base,
                    "Status": status,
                    "Reason": reason,
                    "Odoo Partner ID": partner["id"],
                    "Odoo Partner": partner["name"],
                    "Match Method": match_method,
                    "Updated Fields": "; ".join(sorted(updates)),
                }
            )
            continue

        if args.apply:
            partner_id = execute(models, db, uid, api_key, "res.partner", "create", [partner_values(customer, partner_fields)])
            partner = execute(models, db, uid, api_key, "res.partner", "read", [[partner_id]], {"fields": ["id", "name"]})[0]
            status = "Created"
            reason = "Created new customer contact"
        else:
            partner = {"id": "", "name": ""}
            status = "Ready Create"
            reason = "No existing Odoo contact matched"
        rows.append(
            {
                **base,
                "Status": status,
                "Reason": reason,
                "Odoo Partner ID": partner["id"],
                "Odoo Partner": partner["name"],
                "Match Method": match_method,
            }
        )

    write_results(rows)
    write_summary(rows, args.apply)

    counts = defaultdict(int)
    for row in rows:
        counts[row["Status"]] += 1
    print(f"Connected uid: {uid}")
    print(f"Odoo write performed: {'yes' if args.apply else 'no, dry run only'}")
    print(f"Created: {counts['Created']}")
    print(f"Matched existing: {counts['Matched']}")
    print(f"Updated existing: {counts['Updated']}")
    print(f"Skipped: {counts['Skipped']}")
    print(f"Needs review: {counts['Review']}")
    print(f"Results: {OUT}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
