import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PLAN = ROOT / "odoo_imports" / "bank_reconciliation" / "shop_boss_june_deposit_review_decisions.csv"
OUT = ROOT / "odoo_imports" / "bank_reconciliation" / "odoo_shop_boss_simple_deposits_apply_plan.csv"

SIMPLE_APPROVED_BANK_IDS = {"375", "473"}
TARGET_ACCOUNT_NAME = "Parts Revenue"
TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"


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


def approved_rows():
    rows = []
    with PLAN.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["Decision"] == "Approve" and row["Bank Line ID"] in SIMPLE_APPROVED_BANK_IDS:
                rows.append(row)
    return rows


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
    account = single(models, db, uid, api_key, "account.account", account_domain(models, db, uid, api_key, company["id"], TARGET_ACCOUNT_NAME), ["id", "name", "code"], TARGET_ACCOUNT_NAME)

    plan_rows = approved_rows()
    bank_ids = [int(row["Bank Line ID"]) for row in plan_rows]
    bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [("id", "in", bank_ids), ("company_id", "=", company["id"]), ("journal_id", "=", journal["id"]), ("is_reconciled", "=", False)],
        ["id", "date", "payment_ref", "amount", "move_id"],
        limit=100,
        order="date asc,id asc",
    )
    move_ids = [rel_id(row.get("move_id")) for row in bank_lines if rel_id(row.get("move_id"))]
    move_lines = read(models, db, uid, api_key, "account.move.line", [("move_id", "in", move_ids)], ["id", "move_id", "account_id", "debit", "credit", "balance"], limit=1000)
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    rows = []
    writes = []
    for bank in bank_lines:
        suspense = [line for line in by_move.get(rel_id(bank.get("move_id")), []) if rel_name(line.get("account_id")) == "Bank Suspense Account"]
        if len(suspense) != 1:
            continue
        rows.append(
            {
                "Bank Line ID": bank["id"],
                "Date": bank["date"],
                "Payment Ref": bank["payment_ref"],
                "Amount": bank["amount"],
                "Suspense Move Line ID": suspense[0]["id"],
                "New Account": account["name"],
            }
        )
        writes.append(suspense[0]["id"])

    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        fields = ["Bank Line ID", "Date", "Payment Ref", "Amount", "Suspense Move Line ID", "New Account"]
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    if apply:
        for line_id in writes:
            execute(models, db, uid, api_key, "account.move.line", "write", [[line_id], {"account_id": account["id"]}])

    print(f"Connected uid: {uid}")
    print(f"Candidates: {len(rows)}")
    print(f"Applied: {apply}")
    print(f"Plan: {OUT}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
