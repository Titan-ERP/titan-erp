import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
EXPORT = OUT / "odoo_unreconciled_bank_statement_lines_laurel_bank_live.csv"
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


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return value


def flatten(rows):
    out = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            clean[key] = rel(value)
        out.append(clean)
    return out


def main():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = execute(
        models,
        db,
        uid,
        api_key,
        "res.company",
        "search_read",
        [[("name", "=", TARGET_COMPANY_NAME)]],
        {"fields": ["id", "name"], "limit": 2},
    )[0]
    journal = execute(
        models,
        db,
        uid,
        api_key,
        "account.journal",
        "search_read",
        [[("name", "=", TARGET_JOURNAL_NAME), ("company_id", "=", company["id"])]],
        {"fields": ["id", "name"], "limit": 2},
    )[0]
    fields = ["id", "display_name", "date", "payment_ref", "partner_id", "amount", "journal_id", "company_id", "is_reconciled", "move_id"]
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        "search_read",
        [[("company_id", "=", company["id"]), ("journal_id", "=", journal["id"]), ("is_reconciled", "=", False)]],
        {"fields": fields, "limit": 20000, "order": "date asc"},
    )
    OUT.mkdir(parents=True, exist_ok=True)
    with EXPORT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flatten(rows))
    print(f"Connected uid: {uid}")
    print(f"Laurel Bank unreconciled exported: {len(rows)}")
    print(f"Export: {EXPORT}")


if __name__ == "__main__":
    main()
