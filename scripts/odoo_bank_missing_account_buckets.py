import csv
import os
import re
import xmlrpc.client
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
LINES = ROOT / "odoo_imports" / "bank_reconciliation" / "odoo_unreconciled_bank_statement_lines_2026_03_to_06.csv"

RULES = [
    (r"UPS|FEDEX|USPS", "Shipping"),
    (r"MURPHY USA|TEXACO|EXXON|SHELL|CHEVRON|FUEL", "Fuel"),
    (r"AMAZON|OFFICE DEPOT|STAPLES", "Office"),
    (r"MONTHLY DEBIT CARD FEE|SERVICE CHARGE|ANALYSIS CHARGE|BANK FEE", "Bank Fees"),
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


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def classify(ref):
    text = str(ref or "").upper()
    for pattern, label in RULES:
        if re.search(pattern, text):
            return label
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
    with LINES.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if classify(row.get("payment_ref")):
                ids.append(int(row["id"]))
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        "search_read",
        [[("id", "in", ids), ("is_reconciled", "=", False)]],
        {"fields": ["id", "date", "payment_ref", "amount", "company_id"], "limit": 1000, "order": "date asc"},
    )
    counts = Counter((classify(row["payment_ref"]), rel(row["company_id"])) for row in rows)
    for (label, company), count in sorted(counts.items()):
        print(f"{label}\t{company}\t{count}")
    print("\nExamples")
    for row in rows[:60]:
        print(f"{classify(row['payment_ref'])}\t{rel(row['company_id'])}\t{row['date']}\t{row['amount']}\t{row['payment_ref']}")


if __name__ == "__main__":
    main()
