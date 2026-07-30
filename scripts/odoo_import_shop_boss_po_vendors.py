import argparse
import csv
import html
import os
import re
import sys
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SRC = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_po_open_current_2026.csv"
CRM_DIR = ROOT / "odoo_imports" / "crm"
OUT = CRM_DIR / "shop_boss_po_vendor_import_results_2026.csv"
SUMMARY = CRM_DIR / "shop_boss_po_vendor_import_summary_2026.md"
MARKER = "Shop Boss PO vendor import - 2026"

NAME_OVERRIDES = {
    "AG UP LAUREL": "AG UP- Laurel",
    "AIC REPLACEMENT PARTS": "AIC Replacement Parts",
    "BLUMAQ CORP": "Blumaq Corp",
    "D AND B ELECTRICAL": "D & B Electrical",
    "D AND W DIESEL": "D & W Diesel",
    "H AND R AGRI POWER": "H&R Agri-Power",
    "ITR AMERICA": "ITR America",
    "KSP SPARE PARTS": "KSP Spare Parts",
    "NAPA LAUREL": "NAPA (Laurel)",
    "RAYS USED EQUIPMENT": "Rays Used Equipment",
    "SMA": "SMA",
    "SPAREX": "Sparex",
    "TVH PARTS CO": "TVH Parts Co.",
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


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def format_money(value):
    value = money(value)
    return f"${value:,.2f}"


def norm(value):
    raw = str(value or "").upper().replace("&", " AND ")
    raw = raw.replace(".", " ").replace("(", " ").replace(")", " ")
    text = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def display_name(value):
    normalized = norm(value)
    if normalized in NAME_OVERRIDES:
        return NAME_OVERRIDES[normalized]
    words = []
    for word in normalized.split():
        if len(word) <= 3 and word.isalpha():
            words.append(word)
        else:
            words.append(word[:1] + word[1:].lower())
    return " ".join(words)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def source_vendors():
    grouped = defaultdict(lambda: {"supplier": "", "po_count": 0, "total": Decimal("0.00"), "first_date": "", "last_date": "", "statuses": defaultdict(int), "pos": []})
    for row in read_csv(SRC):
        supplier = (row.get("supplier") or "").strip()
        if not supplier:
            continue
        key = norm(supplier)
        item = grouped[key]
        item["supplier"] = item["supplier"] or supplier
        item["po_count"] += 1
        item["total"] += money(row.get("total_po"))
        date = row.get("date_issue") or ""
        if date:
            item["first_date"] = min([d for d in [item["first_date"], date] if d])
            item["last_date"] = max([d for d in [item["last_date"], date] if d])
        status = row.get("status") or "Unknown"
        item["statuses"][status] += 1
        item["pos"].append(
            {
                "po_number": row.get("po_number") or "",
                "date_issue": date,
                "status": status,
                "total_po": money(row.get("total_po")),
                "po_type": row.get("po_type") or row.get("type") or "",
            }
        )
    vendors = []
    for key, item in grouped.items():
        vendors.append(
            {
                "normalized": key,
                "source_supplier": item["supplier"],
                "odoo_name": display_name(item["supplier"]),
                "po_count": item["po_count"],
                "total": item["total"],
                "first_date": item["first_date"],
                "last_date": item["last_date"],
                "statuses": dict(item["statuses"]),
                "pos": sorted(item["pos"], key=lambda po: (po["date_issue"], po["po_number"])),
            }
        )
    return sorted(vendors, key=lambda vendor: vendor["odoo_name"])


def load_existing_partners(models, db, uid, api_key):
    fields = ["id", "name", "supplier_rank", "customer_rank", "ref", "comment"]
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
    with_marker = [row for row in matches if MARKER in str(row.get("comment") or "")]
    if len(with_marker) == 1:
        return with_marker[0], "previous PO vendor import marker"
    vendors = [row for row in matches if int(row.get("supplier_rank") or 0) > 0]
    if len(vendors) == 1:
        return vendors[0], "supplier-ranked duplicate normalized name"
    return None, "multiple normalized name matches"


def vendor_note(vendor):
    status_text = "; ".join(f"{status}: {count}" for status, count in sorted(vendor["statuses"].items())) or "None"
    po_lines = "".join(
        f"<li>PO {html.escape(po['po_number'])}: {html.escape(po['date_issue'])}, {html.escape(po['status'])}, {format_money(po['total_po'])}</li>"
        for po in vendor["pos"]
    )
    return (
        f"<p><strong>{MARKER}</strong></p>"
        "<ul>"
        f"<li>Source supplier: {html.escape(vendor['source_supplier'])}</li>"
        f"<li>PO count: {vendor['po_count']}</li>"
        f"<li>Total open/current PO value: {format_money(vendor['total'])}</li>"
        f"<li>Date range: {html.escape(vendor['first_date'])} to {html.escape(vendor['last_date'])}</li>"
        f"<li>Status counts: {html.escape(status_text)}</li>"
        "</ul>"
        "<p><strong>Shop Boss PO records</strong></p><ul>"
        f"{po_lines}"
        "</ul>"
    )


def create_values(vendor, field_map):
    values = {"name": vendor["odoo_name"]}
    if "supplier_rank" in field_map:
        values["supplier_rank"] = 1
    if "is_company" in field_map:
        values["is_company"] = True
    if "ref" in field_map:
        values["ref"] = f"Shop Boss PO Vendor: {vendor['source_supplier']}"
    if "comment" in field_map:
        values["comment"] = vendor_note(vendor)
    return values


def update_values(vendor, partner, field_map):
    values = {}
    if "supplier_rank" in field_map and int(partner.get("supplier_rank") or 0) < 1:
        values["supplier_rank"] = 1
    if "ref" in field_map and not partner.get("ref"):
        values["ref"] = f"Shop Boss PO Vendor: {vendor['source_supplier']}"
    if "comment" in field_map and MARKER not in str(partner.get("comment") or ""):
        existing = str(partner.get("comment") or "")
        values["comment"] = existing + vendor_note(vendor) if existing else vendor_note(vendor)
    return values


def write_results(rows):
    CRM_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "Status",
        "Reason",
        "Shop Boss Supplier",
        "Odoo Vendor Name",
        "PO Count",
        "PO Total",
        "First PO Date",
        "Last PO Date",
        "Odoo Partner ID",
        "Odoo Partner",
        "Match Method",
        "Updated Fields",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows, apply):
    counts = defaultdict(int)
    for row in rows:
        counts[row["Status"]] += 1
    SUMMARY.write_text(
        f"""# Shop Boss PO Vendor Import Summary

Source: `odoo_imports/shop_boss/shop_boss_po_open_current_2026.csv`

- Odoo write performed: {'yes' if apply else 'no, dry run only'}
- Created: {counts['Created']}
- Updated existing: {counts['Updated']}
- Matched existing: {counts['Matched']}
- Ready create: {counts['Ready Create']}
- Ready update: {counts['Ready Update']}
- Review: {counts['Review']}

Results: `odoo_imports/crm/shop_boss_po_vendor_import_results_2026.csv`
""",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser(description="Create/update Odoo vendors from Shop Boss PO supplier data.")
    parser.add_argument("--apply", action="store_true", help="Create/update Odoo vendors. Default is dry run.")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    field_map = partner_fields(models, db, uid, api_key)
    existing = load_existing_partners(models, db, uid, api_key)
    results = []
    for vendor in source_vendors():
        partner, match_method = choose_existing(existing.get(norm(vendor["odoo_name"]), []))
        base = {
            "Shop Boss Supplier": vendor["source_supplier"],
            "Odoo Vendor Name": vendor["odoo_name"],
            "PO Count": vendor["po_count"],
            "PO Total": format_money(vendor["total"]),
            "First PO Date": vendor["first_date"],
            "Last PO Date": vendor["last_date"],
            "Match Method": match_method,
        }
        if match_method.startswith("multiple"):
            results.append({**base, "Status": "Review", "Reason": match_method})
            continue
        if partner:
            values = update_values(vendor, partner, field_map)
            if values and args.apply:
                execute(models, db, uid, api_key, "res.partner", "write", [[partner["id"]], values])
                status = "Updated"
                reason = "Matched existing partner and filled vendor PO data"
            elif values:
                status = "Ready Update"
                reason = "Matched existing partner with missing vendor PO data"
            else:
                status = "Matched"
                reason = "Existing Odoo vendor already has PO data"
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
            partner_id = execute(models, db, uid, api_key, "res.partner", "create", [create_values(vendor, field_map)])
            partner = execute(models, db, uid, api_key, "res.partner", "read", [[partner_id]], {"fields": ["id", "name"]})[0]
            existing[norm(vendor["odoo_name"])].append(partner)
            status = "Created"
            reason = "Created new PO vendor"
        else:
            partner = {"id": "", "name": ""}
            status = "Ready Create"
            reason = "No existing Odoo partner matched"
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
    write_summary(results, args.apply)
    counts = defaultdict(int)
    for row in results:
        counts[row["Status"]] += 1
    print(f"Odoo write performed: {'yes' if args.apply else 'no, dry run only'}")
    print(f"Shop Boss PO vendors: {len(results)}")
    print(f"Created: {counts['Created']}")
    print(f"Updated existing: {counts['Updated']}")
    print(f"Matched existing: {counts['Matched']}")
    print(f"Ready create: {counts['Ready Create']}")
    print(f"Ready update: {counts['Ready Update']}")
    print(f"Review: {counts['Review']}")
    print(f"Results: {OUT}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
