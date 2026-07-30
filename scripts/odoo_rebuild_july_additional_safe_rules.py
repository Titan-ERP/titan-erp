import csv
import os
import re
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "july_bank_additional_safe_rebuild_results.csv"
COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Bank"

RULES = [
    ("Bank Merchant Fees", [r"MONTHLY DEBIT CARD FEE", r"BANKCARD-1205/MTOT DEP"]),
    ("Sales Tax Payable", [r"IRS/USATAXPYMT", r"MSDEPTOFREVENUE/TAXPAYMENT"]),
    ("Parts COGS", [r"FRIDAYPARTS", r"COLE TRACTOR", r"SQ \*WEST VIRGINIA MANUFAC"]),
    ("Software Subscriptions", [r"VONAGE BUSINESS", r"WWW\.SMALINK\.COM"]),
    ("Office Expenses", [r"WAL WAL-MART", r"USPS PO"]),
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


def rel_id(value):
    return value[0] if isinstance(value, list) and value else False


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def account_domain(models, db, uid, api_key, company_id, account_name):
    fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    domain = [("name", "=", account_name)]
    if "company_ids" in fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in fields:
        domain.append(("company_id", "=", company_id))
    return domain


def write_csv(rows):
    fields = [
        "Status", "Action", "Bank Statement Line ID", "Date", "Amount", "Payment Ref",
        "New Account", "Suspense Move Line ID", "After Reconciled", "Reason",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def matched_account(ref):
    for account, patterns in RULES:
        if any(re.search(pattern, ref, re.I) for pattern in patterns):
            return account
    return ""


def main():
    apply = "--apply" in os.sys.argv
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})[0]
    journal = execute(models, db, uid, api_key, "account.journal", "search_read", [[("name", "=", JOURNAL), ("company_id", "=", company["id"])]], {"fields": ["id"], "limit": 1})[0]
    accounts = {}
    for account_name, _patterns in RULES:
        if account_name in accounts:
            continue
        rows = execute(
            models, db, uid, api_key, "account.account", "search_read",
            [account_domain(models, db, uid, api_key, company["id"], account_name)],
            {"fields": ["id", "name"], "limit": 2},
        )
        if len(rows) != 1:
            raise SystemExit(f"Expected one account {account_name}; found {len(rows)}")
        accounts[account_name] = rows[0]["id"]

    bank_lines = execute(
        models, db, uid, api_key, "account.bank.statement.line", "search_read",
        [[
            ("company_id", "=", company["id"]),
            ("journal_id", "=", journal["id"]),
            ("date", ">=", "2026-07-01"),
            ("date", "<", "2026-08-01"),
            ("is_reconciled", "=", False),
        ]],
        {"fields": ["id", "date", "amount", "payment_ref", "move_id"], "limit": 20000, "order": "date asc,id asc"},
    )
    candidates = []
    for line in bank_lines:
        account = matched_account(line.get("payment_ref") or "")
        if account:
            candidates.append((line, account))

    move_ids = [rel_id(line["move_id"]) for line, _account in candidates if rel_id(line.get("move_id"))]
    move_lines = execute(
        models, db, uid, api_key, "account.move.line", "search_read",
        [[("move_id", "in", move_ids)]],
        {"fields": ["id", "move_id", "account_id"], "limit": 50000},
    )
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    results = []
    for bank, account in candidates:
        suspense = [
            line for line in by_move.get(rel_id(bank["move_id"]), [])
            if rel_name(line.get("account_id")) == "Bank Suspense Account"
        ]
        base = {
            "Bank Statement Line ID": bank["id"],
            "Date": bank.get("date", ""),
            "Amount": bank.get("amount", 0),
            "Payment Ref": bank.get("payment_ref", ""),
            "New Account": account,
        }
        if len(suspense) != 1:
            results.append({**base, "Status": "Review", "Action": "none", "Reason": f"Expected one suspense line; found {len(suspense)}."})
            continue
        base["Suspense Move Line ID"] = suspense[0]["id"]
        if apply:
            execute(models, db, uid, api_key, "account.move.line", "write", [[suspense[0]["id"]], {"account_id": accounts[account]}])
            after = execute(models, db, uid, api_key, "account.bank.statement.line", "read", [[bank["id"]]], {"fields": ["is_reconciled"]})[0]
            results.append({
                **base,
                "Status": "Rebuilt" if after["is_reconciled"] else "Review",
                "Action": "apply_safe_reference_rule",
                "After Reconciled": after["is_reconciled"],
                "Reason": "Applied conservative reference-based account rule.",
            })
        else:
            results.append({
                **base,
                "Status": "Ready",
                "Action": "apply_safe_reference_rule",
                "Reason": "Would apply conservative reference-based account rule.",
            })

    write_csv(results)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for row in results:
        print(row)


if __name__ == "__main__":
    main()
