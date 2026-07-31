import argparse
import csv
import os
import re
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
PLAN = OUT / "odoo_july_safe_bank_items_plan.csv"

TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"
START_DATE = "2026-07-01"
END_DATE = "2026-07-31"

RULES = [
    {"account": "Parts COGS", "patterns": [r"DB ELECTRICAL", r"COLEMAN EQUIPMENT", r"CLARK MACHINERY"]},
    {"account": "Facility Expense", "patterns": [r"Dixie Electric"]},
]


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read(models, db, uid, api_key, model, domain, fields, limit=10000, order=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def rel_id(value):
    return value[0] if isinstance(value, list) and value else False


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def single(models, db, uid, api_key, model, domain, fields, label):
    rows = read(models, db, uid, api_key, model, domain, fields, limit=2)
    if len(rows) != 1:
        raise SystemExit(f"Expected one {label}; found {len(rows)}")
    return rows[0]


def account_domain(models, db, uid, api_key, company_id, account_name):
    fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    domain = [("name", "=", account_name)]
    if "company_ids" in fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in fields:
        domain.append(("company_id", "=", company_id))
    return domain


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = single(models, db, uid, api_key, "res.company", [("name", "=", TARGET_COMPANY_NAME)], ["id", "name"], TARGET_COMPANY_NAME)
    journal = single(models, db, uid, api_key, "account.journal", [("name", "=", TARGET_JOURNAL_NAME), ("company_id", "=", company["id"])], ["id", "name"], TARGET_JOURNAL_NAME)
    accounts = {
        rule["account"]: single(models, db, uid, api_key, "account.account", account_domain(models, db, uid, api_key, company["id"], rule["account"]), ["id", "name", "code"], rule["account"])
        for rule in RULES
    }
    compiled = [
        {"account": rule["account"], "account_id": accounts[rule["account"]]["id"], "regex": re.compile("|".join(rule["patterns"]), re.I)}
        for rule in RULES
    ]

    bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [("company_id", "=", company["id"]), ("journal_id", "=", journal["id"]), ("is_reconciled", "=", False), ("date", ">=", START_DATE), ("date", "<=", END_DATE)],
        ["id", "date", "payment_ref", "amount", "move_id"],
        limit=1000,
        order="date asc,id asc",
    )

    matches = []
    for bank in bank_lines:
        ref = bank.get("payment_ref") or ""
        for rule in compiled:
            if rule["regex"].search(ref):
                matches.append((bank, rule))
                break

    move_ids = [rel_id(bank.get("move_id")) for bank, _ in matches if rel_id(bank.get("move_id"))]
    move_lines = read(models, db, uid, api_key, "account.move.line", [("move_id", "in", move_ids)], ["id", "move_id", "account_id"], limit=1000)
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    rows = []
    writes = []
    for bank, rule in matches:
        suspense = [line for line in by_move.get(rel_id(bank.get("move_id")), []) if rel_name(line.get("account_id")) == "Bank Suspense Account"]
        if len(suspense) != 1:
            continue
        rows.append({"Bank Line ID": bank["id"], "Date": bank["date"], "Payment Ref": bank["payment_ref"], "Amount": bank["amount"], "New Account": rule["account"], "Suspense Move Line ID": suspense[0]["id"]})
        writes.append((suspense[0]["id"], rule["account_id"]))

    with PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["Bank Line ID", "Date", "Payment Ref", "Amount", "New Account", "Suspense Move Line ID"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    if args.apply:
        for line_id, account_id in writes:
            execute(models, db, uid, api_key, "account.move.line", "write", [[line_id], {"account_id": account_id}])

    print(f"Connected uid: {uid}")
    print(f"July candidate lines: {len(rows)}")
    print(f"Applied: {args.apply}")
    print(f"Plan: {PLAN}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
