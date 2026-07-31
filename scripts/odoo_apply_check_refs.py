import csv
import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
MATCHES = ROOT / "odoo_imports" / "bank_reconciliation" / "bank_check_detail_matches.csv"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def main():
    apply = "--apply" in sys.argv
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    updates = []
    with MATCHES.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row.get("Status") != "Matched" or not row.get("Bank Statement Line ID"):
                continue
            ref = row["Suggested Ref"]
            if row.get("Memo"):
                ref = f"{ref} ({row['Memo']})"
            updates.append((int(row["Bank Statement Line ID"]), ref))

    if apply:
        for line_id, ref in updates:
            execute(models, db, uid, api_key, "account.bank.statement.line", "write", [[line_id], {"payment_ref": ref}])

    print(f"Connected uid: {uid}")
    print(f"Matched check refs ready: {len(updates)}")
    print(f"Applied: {apply}")


if __name__ == "__main__":
    main()
