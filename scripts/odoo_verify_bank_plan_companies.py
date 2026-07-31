import csv
import os
import xmlrpc.client
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PLAN = ROOT / "odoo_imports" / "bank_reconciliation" / "odoo_bank_simple_expense_coding_plan.csv"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
    ids = []
    with PLAN.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("Bank Statement Line ID"):
                ids.append(int(row["Bank Statement Line ID"]))
    rows = models.execute_kw(
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        "search_read",
        [[("id", "in", ids)]],
        {"fields": ["id", "date", "payment_ref", "amount", "is_reconciled", "company_id"], "limit": 1000},
    )
    counts = Counter(rel(row.get("company_id")) for row in rows)
    rec_counts = Counter((rel(row.get("company_id")), str(row.get("is_reconciled"))) for row in rows)
    print(f"Plan rows checked: {len(rows)}")
    print(f"By company: {dict(counts)}")
    print(f"By company/reconciled: {dict(rec_counts)}")


if __name__ == "__main__":
    main()
