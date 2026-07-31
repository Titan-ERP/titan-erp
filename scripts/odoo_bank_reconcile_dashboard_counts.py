import os
import xmlrpc.client
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def main():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    rows = execute(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        "search_read",
        [[("is_reconciled", "=", False)]],
        {"fields": ["id", "date", "journal_id", "company_id", "amount", "payment_ref"], "limit": 20000, "order": "date asc"},
    )
    print(f"Connected uid: {uid}")
    print(f"Unreconciled bank statement lines, all dates/all companies: {len(rows)}")
    by_journal = Counter(rel(row.get("journal_id")) for row in rows)
    by_company = Counter(rel(row.get("company_id")) for row in rows)
    by_month = Counter(str(row.get("date", ""))[:7] for row in rows)
    by_journal_company = Counter((rel(row.get("journal_id")), rel(row.get("company_id"))) for row in rows)
    print(f"By journal: {dict(by_journal)}")
    print(f"By company: {dict(by_company)}")
    print(f"By journal/company: {dict(by_journal_company)}")
    print("By month:")
    for month, count in sorted(by_month.items()):
        print(f"  {month}: {count}")


if __name__ == "__main__":
    main()
