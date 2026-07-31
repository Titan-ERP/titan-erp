import csv
import os
import re
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
PLAN = OUT / "odoo_admin_payroll_checks_plan.csv"

TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"
TARGET_ACCOUNT_NAME = "Administrative Payroll"
PAYEE_PATTERNS = [r"Raymy J Holdings", r"Dial Capital"]


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
    if isinstance(value, list) and value:
        return value[0]
    return False


def rel_name(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def single(models, db, uid, api_key, model, domain, fields, label):
    rows = read(models, db, uid, api_key, model, domain, fields, limit=2)
    if len(rows) != 1:
        raise SystemExit(f"Expected one {label}; found {len(rows)}")
    return rows[0]


def target_account(models, db, uid, api_key, company_id):
    account_fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    domain = [("name", "=", TARGET_ACCOUNT_NAME)]
    if "company_ids" in account_fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in account_fields:
        domain.append(("company_id", "=", company_id))
    return single(models, db, uid, api_key, "account.account", domain, ["id", "name", "code"], TARGET_ACCOUNT_NAME)


def main():
    apply = "--apply" in sys.argv
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
    journal = single(
        models,
        db,
        uid,
        api_key,
        "account.journal",
        [("name", "=", TARGET_JOURNAL_NAME), ("company_id", "=", company["id"])],
        ["id", "name"],
        f"{TARGET_COMPANY_NAME} / {TARGET_JOURNAL_NAME}",
    )
    account = target_account(models, db, uid, api_key, company["id"])

    domain = [
        ("company_id", "=", company["id"]),
        ("journal_id", "=", journal["id"]),
        ("is_reconciled", "=", False),
    ]
    payee_domain = []
    for idx, pattern in enumerate(PAYEE_PATTERNS):
        if idx:
            payee_domain.insert(0, "|")
        payee_domain.append(("payment_ref", "ilike", pattern))

    bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        domain + payee_domain,
        ["id", "date", "payment_ref", "amount", "move_id"],
        limit=1000,
        order="date asc",
    )
    pattern_re = re.compile("|".join(PAYEE_PATTERNS), re.I)
    bank_lines = [line for line in bank_lines if pattern_re.search(line.get("payment_ref") or "")]

    move_ids = [rel_id(line.get("move_id")) for line in bank_lines if rel_id(line.get("move_id"))]
    move_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        [("move_id", "in", move_ids)],
        ["id", "move_id", "account_id", "debit", "credit", "balance"],
        limit=max(10000, len(move_ids) * 5),
        order="id asc",
    )
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    plan_rows = []
    writes = []
    for bank in bank_lines:
        suspense_lines = [
            line for line in by_move.get(rel_id(bank.get("move_id")), [])
            if rel_name(line.get("account_id")) == "Bank Suspense Account"
        ]
        if len(suspense_lines) != 1:
            continue
        suspense = suspense_lines[0]
        writes.append((suspense["id"], account["id"]))
        plan_rows.append(
            {
                "Bank Statement Line ID": bank["id"],
                "Date": bank.get("date", ""),
                "Payment Ref": bank.get("payment_ref", ""),
                "Amount": bank.get("amount", ""),
                "Suspense Move Line ID": suspense["id"],
                "New Account ID": account["id"],
                "New Account": f"{account.get('code') or ''} {account['name']}".strip(),
            }
        )

    with PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["Bank Statement Line ID", "Date", "Payment Ref", "Amount", "Suspense Move Line ID", "New Account ID", "New Account"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan_rows)

    if apply:
        for move_line_id, account_id in writes:
            execute(models, db, uid, api_key, "account.move.line", "write", [[move_line_id], {"account_id": account_id}])

    print(f"Connected uid: {uid}")
    print(f"Target account: {account.get('code') or ''} {account['name']}")
    print(f"Candidate lines: {len(plan_rows)}")
    print(f"Applied: {apply}")
    print(f"Plan: {PLAN}")


if __name__ == "__main__":
    main()
