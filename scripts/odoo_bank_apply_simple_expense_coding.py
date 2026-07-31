import csv
import os
import re
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
PLAN = OUT / "odoo_bank_simple_expense_coding_plan.csv"
TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"


RULES = [
    (r"SPAREX|TRACTORPARTS|AICPARTS|TRACTO PARTS|B&M TRACTOR|PAINT VALLEY|CROSS CREEK TRACTOR|FINNEY EQUIPMENT|PUCKETT MACHINERY|COWIN EQUIPMENT|GRAINGER|HB SEALING|HANDR AGRI", "Parts COGS", "Parts/vendor purchase"),
    (r"UPS|FEDEX|USPS", "Postage and Delivery", "Shipping/freight"),
    (r"MURPHY USA|TEXACO|EXXON|SHELL|CHEVRON|FUEL", "Company Vehicle Expense", "Fuel purchase"),
    (r"AMAZON|OFFICE DEPOT|STAPLES", "Office Expenses", "Office/admin purchase"),
    (r"PANDA EXPRESS|RESTAURANT|MCDONALD|CHICK-FIL-A|FOOD", "Meals & Entertainment", "Meals"),
    (r"MONTHLY DEBIT CARD FEE|SERVICE CHARGE|ANALYSIS CHARGE|BANK FEE", "Bank Merchant Fees", "Bank fee"),
]


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env(ENV_PATH)
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read(models, db, uid, api_key, model, domain, fields, limit=10000, order=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def model_fields(models, db, uid, api_key, model):
    return execute(models, db, uid, api_key, model, "fields_get", [], {"attributes": ["string", "type"]})


def rel_id(value):
    if isinstance(value, list) and value:
        return value[0]
    return False


def rel_name(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def target_company_id(models, db, uid, api_key):
    companies = read(
        models,
        db,
        uid,
        api_key,
        "res.company",
        [("name", "=", TARGET_COMPANY_NAME)],
        ["id", "name"],
        limit=2,
    )
    if len(companies) != 1:
        raise SystemExit(f"Expected one company named {TARGET_COMPANY_NAME!r}; found {len(companies)}")
    return companies[0]["id"]


def target_journal_id(models, db, uid, api_key, company_id):
    journals = read(
        models,
        db,
        uid,
        api_key,
        "account.journal",
        [("name", "=", TARGET_JOURNAL_NAME), ("company_id", "=", company_id)],
        ["id", "name"],
        limit=2,
    )
    if len(journals) != 1:
        raise SystemExit(
            f"Expected one journal named {TARGET_JOURNAL_NAME!r} for {TARGET_COMPANY_NAME!r}; found {len(journals)}"
        )
    return journals[0]["id"]


def classify(ref):
    text = str(ref or "").upper()
    for pattern, account, note in RULES:
        if re.search(pattern, text):
            return account, note
    return None, None


def account_map(models, db, uid, api_key, company_id):
    account_fields = model_fields(models, db, uid, api_key, "account.account")
    field_names = ["id", "name", "code"]
    for optional in ["deprecated", "active"]:
        if optional in account_fields:
            field_names.append(optional)
    if "company_ids" in account_fields:
        field_names.append("company_ids")
    if "company_id" in account_fields:
        field_names.append("company_id")
    domain = []
    if "company_ids" in account_fields:
        domain = [("company_ids", "in", [company_id])]
    elif "company_id" in account_fields:
        domain = [("company_id", "=", company_id)]
    accounts = read(
        models,
        db,
        uid,
        api_key,
        "account.account",
        domain,
        field_names,
        limit=10000,
        order="code asc",
    )
    by_name = {}
    for account in accounts:
        if not account.get("deprecated") and account.get("active", True):
            by_name.setdefault(account["name"], account)
    return by_name


def main():
    apply = "--apply" in sys.argv
    db, uid, api_key, models = connect()
    target_company = target_company_id(models, db, uid, api_key)
    target_journal = target_journal_id(models, db, uid, api_key, target_company)

    bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [
            ("is_reconciled", "=", False),
            ("company_id", "=", target_company),
            ("journal_id", "=", target_journal),
        ],
        ["id", "date", "payment_ref", "amount", "move_id", "company_id"],
        order="date asc",
    )
    company_ids = sorted({rel_id(row.get("company_id")) for row in bank_lines if rel_id(row.get("company_id"))})
    accounts_by_company = {
        company_id: account_map(models, db, uid, api_key, company_id)
        for company_id in company_ids
    }
    move_ids = [rel_id(row["move_id"]) for row in bank_lines if rel_id(row.get("move_id"))]
    move_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        [("move_id", "in", move_ids)],
        ["id", "move_id", "account_id", "partner_id", "debit", "credit", "balance"],
        limit=max(10000, len(move_ids) * 5),
        order="id asc",
    )
    by_move = {}
    for ml in move_lines:
        by_move.setdefault(rel_id(ml["move_id"]), []).append(ml)

    plan_rows = []
    writes = []
    missing_accounts = set()
    for bank in bank_lines:
        account_name, note = classify(bank.get("payment_ref"))
        if not account_name:
            continue
        company_id = rel_id(bank.get("company_id"))
        account = accounts_by_company.get(company_id, {}).get(account_name)
        if not account:
            missing_accounts.add(account_name)
            continue
        suspense = [
            ml for ml in by_move.get(rel_id(bank.get("move_id")), [])
            if rel_name(ml.get("account_id")) == "Bank Suspense Account"
        ]
        if len(suspense) != 1:
            continue
        suspense_line = suspense[0]
        writes.append((suspense_line["id"], account["id"]))
        plan_rows.append(
            {
                "Bank Statement Line ID": bank["id"],
                "Date": bank.get("date", ""),
                "Payment Ref": bank.get("payment_ref", ""),
                "Amount": bank.get("amount", ""),
                "Suspense Move Line ID": suspense_line["id"],
                "New Account ID": account["id"],
                "New Account": f"{account.get('code', '')} {account['name']}".strip(),
                "Reason": note,
            }
        )

    with PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "Bank Statement Line ID",
                "Date",
                "Payment Ref",
                "Amount",
                "Suspense Move Line ID",
                "New Account ID",
                "New Account",
                "Reason",
            ],
        )
        writer.writeheader()
        writer.writerows(plan_rows)

    if apply:
        for move_line_id, account_id in writes:
            execute(models, db, uid, api_key, "account.move.line", "write", [[move_line_id], {"account_id": account_id}])

    print(f"Connected uid: {uid}")
    print(f"Simple expense coding candidates: {len(plan_rows)}")
    print(f"Missing accounts: {sorted(missing_accounts)}")
    print(f"Applied: {apply}")
    print(f"Plan: {PLAN}")


if __name__ == "__main__":
    main()
