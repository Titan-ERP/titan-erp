import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PAYMENTS = ROOT / "odoo_imports" / "shop_boss" / "odoo_shop_boss_july_payment_registration_results.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_shop_boss_july_payment_move_lines.csv"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def rel(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def rel_id(value):
    return value[0] if isinstance(value, list) and value else False


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

    payment_ids = sorted({int(row["Payment ID"]) for row in read_csv(PAYMENTS) if row.get("Payment ID")})
    payments = execute(
        models, db, uid, api_key, "account.payment", "read",
        [payment_ids],
        {"fields": ["id", "name", "date", "amount", "journal_id", "move_id", "partner_id", "is_reconciled"]},
    )
    move_ids = [rel_id(row["move_id"]) for row in payments if rel_id(row.get("move_id"))]
    lines = execute(
        models, db, uid, api_key, "account.move.line", "search_read",
        [[("move_id", "in", move_ids)]],
        {"fields": ["id", "move_id", "date", "name", "partner_id", "account_id", "debit", "credit", "balance", "amount_residual", "reconciled", "matching_number"], "limit": 50000, "order": "move_id,id"},
    )
    payment_by_move = {rel_id(row["move_id"]): row for row in payments}
    rows = []
    for line in lines:
        payment = payment_by_move.get(rel_id(line["move_id"]), {})
        rows.append({
            "Payment ID": payment.get("id", ""),
            "Payment": payment.get("name", ""),
            "Payment Date": payment.get("date", ""),
            "Payment Amount": payment.get("amount", ""),
            "Payment Journal": rel(payment.get("journal_id")),
            "Payment Partner": rel(payment.get("partner_id")),
            "Payment Is Reconciled": payment.get("is_reconciled", ""),
            "Move Line ID": line["id"],
            "Move": rel(line.get("move_id")),
            "Line Date": line.get("date", ""),
            "Line Name": line.get("name", ""),
            "Line Partner": rel(line.get("partner_id")),
            "Account": rel(line.get("account_id")),
            "Debit": line.get("debit", 0),
            "Credit": line.get("credit", 0),
            "Balance": line.get("balance", 0),
            "Residual": line.get("amount_residual", 0),
            "Reconciled": line.get("reconciled", False),
            "Matching Number": line.get("matching_number", ""),
        })

    fields = list(rows[0].keys()) if rows else []
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)
    print(f"Connected uid: {uid}")
    print(f"Payments: {len(payments)}")
    print(f"Move lines: {len(rows)}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
