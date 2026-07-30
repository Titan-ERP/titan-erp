import os
import xmlrpc.client
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"

TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"
BANK_LINE_ID = 561
SOURCE_LABEL = "Shop Boss RO 1108; RO 1111; Part Sales 394; Part Sales 397; Part Sales 407"

SPLIT = {
    "Parts Revenue": Decimal("890.92"),
    "Service Revenue": Decimal("541.77"),
    "Sales Tax Payable": Decimal("61.45"),
    "Bank Merchant Fees": Decimal("52.32"),
}


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

    company = single(models, db, uid, api_key, "res.company", [("name", "=", TARGET_COMPANY_NAME)], ["id", "name"], TARGET_COMPANY_NAME)
    journal = single(models, db, uid, api_key, "account.journal", [("name", "=", TARGET_JOURNAL_NAME), ("company_id", "=", company["id"])], ["id", "name"], TARGET_JOURNAL_NAME)
    accounts = {
        name: single(models, db, uid, api_key, "account.account", account_domain(models, db, uid, api_key, company["id"], name), ["id", "name"], name)
        for name in SPLIT
    }

    bank = single(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [("id", "=", BANK_LINE_ID), ("company_id", "=", company["id"]), ("journal_id", "=", journal["id"]), ("is_reconciled", "=", False)],
        ["id", "date", "payment_ref", "amount", "move_id"],
        f"bank line {BANK_LINE_ID}",
    )
    move_id = rel_id(bank["move_id"])
    lines = read(models, db, uid, api_key, "account.move.line", [("move_id", "=", move_id)], ["id", "account_id", "debit", "credit"], limit=100)
    suspense = [line for line in lines if rel_name(line["account_id"]) == "Bank Suspense Account"]
    already_split = [line for line in lines if rel_name(line["account_id"]) in SPLIT]
    if len(suspense) != 1 or already_split:
        raise SystemExit("Move is not in the expected unsplit suspense state.")

    bank_amount = Decimal(str(bank["amount"]))
    debits = bank_amount + SPLIT["Bank Merchant Fees"]
    credits = SPLIT["Parts Revenue"] + SPLIT["Service Revenue"] + SPLIT["Sales Tax Payable"]
    if debits != credits:
        raise SystemExit(f"Split does not balance: debits {debits}, credits {credits}")

    print(f"Connected uid: {uid}")
    print(f"Bank line: {bank}")
    print(f"Debit bank: {bank_amount}; debit merchant fees: {SPLIT['Bank Merchant Fees']}")
    print(f"Credit parts: {SPLIT['Parts Revenue']}; service: {SPLIT['Service Revenue']}; tax: {SPLIT['Sales Tax Payable']}")
    print(f"Applied: {apply}")

    if apply:
        context = {"check_move_validity": False}
        execute(
            models,
            db,
            uid,
            api_key,
            "account.move.line",
            "write",
            [[suspense[0]["id"]], {"account_id": accounts["Parts Revenue"]["id"], "name": SOURCE_LABEL, "debit": 0.0, "credit": float(SPLIT["Parts Revenue"])}],
            {"context": context},
        )
        for account_name in ["Service Revenue", "Sales Tax Payable"]:
            execute(
                models,
                db,
                uid,
                api_key,
                "account.move.line",
                "create",
                [{"move_id": move_id, "account_id": accounts[account_name]["id"], "name": SOURCE_LABEL, "debit": 0.0, "credit": float(SPLIT[account_name])}],
                {"context": context},
            )
        execute(
            models,
            db,
            uid,
            api_key,
            "account.move.line",
            "create",
            [{"move_id": move_id, "account_id": accounts["Bank Merchant Fees"]["id"], "name": SOURCE_LABEL, "debit": float(SPLIT["Bank Merchant Fees"]), "credit": 0.0}],
            {"context": context},
        )


if __name__ == "__main__":
    main()
