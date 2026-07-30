import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "live_laurel_bank_statement_lines_2026_07.csv"
COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Bank"


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
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})[0]
    journal = execute(
        models, db, uid, api_key, "account.journal", "search_read",
        [[("name", "=", JOURNAL), ("company_id", "=", company["id"])]],
        {"fields": ["id"], "limit": 1},
    )[0]
    fields = ["id", "date", "journal_id", "company_id", "amount", "payment_ref", "partner_id", "move_id", "is_reconciled"]
    rows = execute(
        models, db, uid, api_key, "account.bank.statement.line", "search_read",
        [[
            ("company_id", "=", company["id"]),
            ("journal_id", "=", journal["id"]),
            ("date", ">=", "2026-07-01"),
            ("date", "<", "2026-08-01"),
        ]],
        {"fields": fields, "limit": 20000, "order": "date asc,id asc"},
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "Bank Statement Line ID", "Date", "Company", "Journal", "Partner",
            "Amount", "Payment Ref", "Bank Move", "Is Reconciled",
        ])
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "Bank Statement Line ID": row["id"],
                "Date": row.get("date", ""),
                "Company": rel(row.get("company_id")),
                "Journal": rel(row.get("journal_id")),
                "Partner": rel(row.get("partner_id")),
                "Amount": row.get("amount", 0),
                "Payment Ref": row.get("payment_ref", ""),
                "Bank Move": rel(row.get("move_id")),
                "Is Reconciled": row.get("is_reconciled", False),
            })
    print(f"Connected uid: {uid}")
    print(f"Rows: {len(rows)}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
