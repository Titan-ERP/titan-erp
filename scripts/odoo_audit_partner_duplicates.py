import csv
import os
import re
import sys
import xmlrpc.client
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
CRM_DIR = ROOT / "odoo_imports" / "crm"
OUT = CRM_DIR / "odoo_partner_duplicate_audit.csv"
SUMMARY = CRM_DIR / "odoo_partner_duplicate_audit_summary.md"


def load_env(path):
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


def norm_name(value):
    raw = str(value or "").upper().replace("&", " AND ")
    raw = re.sub(r"\s+-\s+V$", "", raw)
    raw = raw.replace("'", "")
    text = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def phone_digits(*values):
    for value in values:
        digits = re.sub(r"\D+", "", str(value or ""))
        if len(digits) == 11 and digits.startswith("1"):
            digits = digits[1:]
        if len(digits) >= 7:
            return digits
    return ""


def read_import_partner_ids():
    paths = [
        CRM_DIR / "pasted_customer_directory_import_results.csv",
        CRM_DIR / "shop_boss_customer_import_results_2026_07.csv",
        CRM_DIR / "shop_boss_po_vendor_import_results_2026.csv",
    ]
    ids = set()
    for path in paths:
        if not path.exists():
            continue
        with path.open("r", newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                value = row.get("Odoo Partner ID")
                if value and value.isdigit():
                    ids.add(int(value))
    return ids


def classify_group(rows, imported_ids):
    active = [row for row in rows if row.get("active")]
    imported = [row for row in rows if row["id"] in imported_ids]
    named = {row.get("name") for row in rows}
    if len(active) <= 1:
        return "Low", "Only one active record; likely archived duplicate residue"
    if len(named) == 1 and len(imported) >= 1:
        return "High", "Same normalized name with multiple active records and import involvement"
    if len(imported) > 1:
        return "High", "Multiple imported records share duplicate key"
    return "Medium", "Multiple active records share duplicate key"


def partner_line(row):
    return f"{row['id']} | {row.get('name') or ''} | customer_rank={row.get('customer_rank') or 0} | supplier_rank={row.get('supplier_rank') or 0} | active={row.get('active')}"


def main():
    db, uid, api_key, models = connect()
    fields = ["id", "name", "active", "phone", "mobile", "email", "customer_rank", "supplier_rank", "parent_id", "ref"]
    available = execute(models, db, uid, api_key, "res.partner", "fields_get", [], {"attributes": ["string"]})
    fields = [field for field in fields if field == "id" or field in available]
    partners = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "search_read",
        [[]],
        {"fields": fields, "limit": 0, "context": {"active_test": False}},
    )
    imported_ids = read_import_partner_ids()

    name_groups = defaultdict(list)
    phone_groups = defaultdict(list)
    for row in partners:
        name_key = norm_name(row.get("name"))
        if name_key:
            name_groups[name_key].append(row)
        phone_key = phone_digits(row.get("phone"), row.get("mobile"))
        if phone_key:
            phone_groups[phone_key].append(row)

    rows = []
    for key, group in sorted(name_groups.items()):
        if len(group) <= 1:
            continue
        severity, reason = classify_group(group, imported_ids)
        rows.append(
            {
                "Duplicate Type": "normalized_name",
                "Key": key,
                "Severity": severity,
                "Reason": reason,
                "Record Count": len(group),
                "Imported Record Count": sum(1 for row in group if row["id"] in imported_ids),
                "Partner IDs": "; ".join(str(row["id"]) for row in group),
                "Partners": "\n".join(partner_line(row) for row in group),
            }
        )
    for key, group in sorted(phone_groups.items()):
        if len(group) <= 1:
            continue
        # Avoid reporting one company with child contacts under the same phone as a high-risk duplicate.
        distinct_names = {norm_name(row.get("name")) for row in group}
        severity, reason = classify_group(group, imported_ids)
        if len(distinct_names) > 1:
            severity = "Medium" if severity == "High" else severity
            reason = "Phone shared by multiple differently named contacts"
        rows.append(
            {
                "Duplicate Type": "phone",
                "Key": key,
                "Severity": severity,
                "Reason": reason,
                "Record Count": len(group),
                "Imported Record Count": sum(1 for row in group if row["id"] in imported_ids),
                "Partner IDs": "; ".join(str(row["id"]) for row in group),
                "Partners": "\n".join(partner_line(row) for row in group),
            }
        )

    CRM_DIR.mkdir(parents=True, exist_ok=True)
    fieldnames = ["Duplicate Type", "Key", "Severity", "Reason", "Record Count", "Imported Record Count", "Partner IDs", "Partners"]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    counts = defaultdict(int)
    for row in rows:
        counts[row["Severity"]] += 1
    SUMMARY.write_text(
        f"""# Odoo Partner Duplicate Audit

- Partners scanned: {len(partners)}
- Imported partner ids referenced: {len(imported_ids)}
- Duplicate groups: {len(rows)}
- High severity groups: {counts['High']}
- Medium severity groups: {counts['Medium']}
- Low severity groups: {counts['Low']}

Results: `odoo_imports/crm/odoo_partner_duplicate_audit.csv`
""",
        encoding="utf-8",
    )

    print(f"Partners scanned: {len(partners)}")
    print(f"Imported partner ids referenced: {len(imported_ids)}")
    print(f"Duplicate groups: {len(rows)}")
    print(f"High severity groups: {counts['High']}")
    print(f"Medium severity groups: {counts['Medium']}")
    print(f"Low severity groups: {counts['Low']}")
    print(f"Results: {OUT}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
