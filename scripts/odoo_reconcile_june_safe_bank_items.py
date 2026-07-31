import argparse
import csv
import os
import re
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
PLAN = OUT / "odoo_june_safe_bank_items_plan.csv"

TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"
START_DATE = "2026-06-01"
END_DATE = "2026-06-30"

RULES = [
    {
        "account": "Parts COGS",
        "patterns": [
            r"CLARK MACHINERY",
            r"DB ELECTRICAL",
            r"TOP GEAR TRACTOR",
            r"KMP USA",
            r"HARBOR FREIGHT",
            r"LAYTON MANUFACTURING",
            r"CIRCLE G TRACTOR PARTS",
            r"MESSICKS",
            r"LEE TRACTOR",
            r"PALMER JOHNSON",
            r"SOUTHERN FARM EQU",
            r"COSTEX",
            r"Memo Corporation",
            r"Delock",
        ],
    },
    {
        "account": "Office Expenses",
        "patterns": [
            r"SHOP BOSS",
            r"OPENAI",
            r"VONAGE",
            r"\bUPS\*",
            r"ULINE \*SHIP SUPPLIES",
        ],
    },
    {
        "account": "Marketing & Advertising",
        "patterns": [
            r"JONES COUNTY CHAMBER",
            r"THE PRINT PRESS",
        ],
    },
    {
        "account": "Sales Tax Payable",
        "patterns": [
            r"MSDEPTOFREVENUE/TAXPAYMENT",
        ],
    },
    {
        "account": "Interest Income",
        "patterns": [
            r"^Interest Payment$",
        ],
    },
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


def account_domain(models, db, uid, api_key, company_id, account_name):
    fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    domain = [("name", "=", account_name)]
    if "company_ids" in fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in fields:
        domain.append(("company_id", "=", company_id))
    return domain


def compile_rules(accounts):
    compiled = []
    for rule in RULES:
        compiled.append(
            {
                "account": rule["account"],
                "account_id": accounts[rule["account"]]["id"],
                "account_display": f"{accounts[rule['account']].get('code') or ''} {rule['account']}".strip(),
                "regex": re.compile("|".join(rule["patterns"]), re.I),
            }
        )
    return compiled


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

    accounts = {}
    for rule in RULES:
        accounts[rule["account"]] = single(
            models,
            db,
            uid,
            api_key,
            "account.account",
            account_domain(models, db, uid, api_key, company["id"], rule["account"]),
            ["id", "name", "code"],
            rule["account"],
        )
    rules = compile_rules(accounts)

    bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [
            ("company_id", "=", company["id"]),
            ("journal_id", "=", journal["id"]),
            ("is_reconciled", "=", False),
            ("date", ">=", START_DATE),
            ("date", "<=", END_DATE),
        ],
        ["id", "date", "payment_ref", "amount", "move_id"],
        limit=10000,
        order="date asc, id asc",
    )

    matches = []
    for bank in bank_lines:
        payment_ref = bank.get("payment_ref") or ""
        matched = None
        for rule in rules:
            if rule["regex"].search(payment_ref):
                matched = rule
                break
        if matched:
            matches.append((bank, matched))

    move_ids = [rel_id(bank.get("move_id")) for bank, _ in matches if rel_id(bank.get("move_id"))]
    move_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        [("move_id", "in", move_ids)],
        ["id", "move_id", "account_id", "debit", "credit", "balance"],
        limit=max(10000, len(move_ids) * 6),
        order="id asc",
    )
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    rows = []
    writes = []
    skipped = 0
    for bank, rule in matches:
        suspense_lines = [
            line
            for line in by_move.get(rel_id(bank.get("move_id")), [])
            if rel_name(line.get("account_id")) == "Bank Suspense Account"
        ]
        if len(suspense_lines) != 1:
            skipped += 1
            continue
        suspense = suspense_lines[0]
        writes.append((suspense["id"], rule["account_id"]))
        rows.append(
            {
                "Bank Statement Line ID": bank["id"],
                "Date": bank.get("date", ""),
                "Payment Ref": bank.get("payment_ref", ""),
                "Amount": bank.get("amount", ""),
                "Suspense Move Line ID": suspense["id"],
                "New Account ID": rule["account_id"],
                "New Account": rule["account_display"],
            }
        )

    OUT.mkdir(parents=True, exist_ok=True)
    with PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["Bank Statement Line ID", "Date", "Payment Ref", "Amount", "Suspense Move Line ID", "New Account ID", "New Account"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    if args.apply:
        for move_line_id, account_id in writes:
            execute(models, db, uid, api_key, "account.move.line", "write", [[move_line_id], {"account_id": account_id}])

    by_account = {}
    for row in rows:
        bucket = by_account.setdefault(row["New Account"], {"count": 0, "amount": 0.0})
        bucket["count"] += 1
        bucket["amount"] += float(row["Amount"] or 0)

    print(f"Connected uid: {uid}")
    print(f"Company: {company['name']} / Journal: {journal['name']}")
    print(f"June candidate lines: {len(rows)}")
    print(f"Skipped no single suspense line: {skipped}")
    print(f"Applied: {args.apply}")
    for account, data in sorted(by_account.items()):
        print(f"{account}: {data['count']} lines, {data['amount']:.2f}")
    print(f"Plan: {PLAN}")


if __name__ == "__main__":
    main()
