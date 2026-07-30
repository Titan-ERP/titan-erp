import csv
import os
import re
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
CRM_DIR = ROOT / "odoo_imports" / "crm"
AUDIT = CRM_DIR / "odoo_partner_duplicate_audit.csv"
OUT = CRM_DIR / "odoo_partner_medium_duplicate_reference_review.csv"
REFERENCE_MODELS = ["account.move", "sale.order", "purchase.order", "account.move.line"]


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


def model_exists(models, db, uid, api_key, model):
    return bool(execute(models, db, uid, api_key, "ir.model", "search_count", [[("model", "=", model)]]))


def ref_counts(models, db, uid, api_key, partner_id):
    counts = {}
    for model in REFERENCE_MODELS:
        counts[model] = 0
        if model_exists(models, db, uid, api_key, model):
            counts[model] = execute(models, db, uid, api_key, model, "search_count", [[("partner_id", "=", partner_id)]])
    return counts


def parse_ids(value):
    return [int(part) for part in re.findall(r"\d+", value or "")]


def total_refs(counts):
    return sum(int(value or 0) for value in counts.values())


def main():
    db, uid, api_key, models = connect()
    rows = []
    with AUDIT.open("r", newline="", encoding="utf-8-sig") as f:
        for duplicate in csv.DictReader(f):
            if duplicate["Severity"] != "Medium" or duplicate["Duplicate Type"] != "normalized_name":
                continue
            ids = parse_ids(duplicate["Partner IDs"])
            partners = execute(
                models,
                db,
                uid,
                api_key,
                "res.partner",
                "read",
                [ids],
                {"fields": ["id", "name", "active", "phone", "email", "street", "ref", "comment", "customer_rank", "supplier_rank", "parent_id"]},
            )
            for partner in partners:
                counts = ref_counts(models, db, uid, api_key, partner["id"])
                rows.append(
                    {
                        "Duplicate Key": duplicate["Key"],
                        "Partner ID": partner["id"],
                        "Name": partner.get("name") or "",
                        "Active": partner.get("active"),
                        "Phone": partner.get("phone") or "",
                        "Email": partner.get("email") or "",
                        "Street": partner.get("street") or "",
                        "Reference": partner.get("ref") or "",
                        "Customer Rank": partner.get("customer_rank") or 0,
                        "Supplier Rank": partner.get("supplier_rank") or 0,
                        "Parent": partner.get("parent_id") or "",
                        "Comment Length": len(str(partner.get("comment") or "")),
                        "Reference Total": total_refs(counts),
                        "Account Moves": counts["account.move"],
                        "Sale Orders": counts["sale.order"],
                        "Purchase Orders": counts["purchase.order"],
                        "Move Lines": counts["account.move.line"],
                    }
                )

    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "Duplicate Key",
            "Partner ID",
            "Name",
            "Active",
            "Phone",
            "Email",
            "Street",
            "Reference",
            "Customer Rank",
            "Supplier Rank",
            "Parent",
            "Comment Length",
            "Reference Total",
            "Account Moves",
            "Sale Orders",
            "Purchase Orders",
            "Move Lines",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    safe_empty = [row for row in rows if str(row["Active"]) == "True" and int(row["Reference Total"]) == 0]
    print(f"Medium normalized-name partner rows reviewed: {len(rows)}")
    print(f"Active rows with zero transactional references: {len(safe_empty)}")
    print(f"Results: {OUT}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
