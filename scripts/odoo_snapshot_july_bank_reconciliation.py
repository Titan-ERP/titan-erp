import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "july_bank_reconciliation_snapshot_before_rebuild.csv"
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


def rel_id(value):
    return value[0] if isinstance(value, list) and value else ""


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


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
    bank_lines = execute(
        models, db, uid, api_key, "account.bank.statement.line", "search_read",
        [[
            ("company_id", "=", company["id"]),
            ("journal_id", "=", journal["id"]),
            ("date", ">=", "2026-07-01"),
            ("date", "<", "2026-08-01"),
        ]],
        {"fields": ["id", "date", "payment_ref", "amount", "is_reconciled", "move_id", "partner_id"], "limit": 20000, "order": "date asc,id asc"},
    )
    move_ids = [rel_id(row.get("move_id")) for row in bank_lines if rel_id(row.get("move_id"))]
    move_lines = execute(
        models, db, uid, api_key, "account.move.line", "search_read",
        [[("move_id", "in", move_ids)]],
        {
            "fields": [
                "id", "move_id", "date", "name", "ref", "partner_id", "account_id",
                "debit", "credit", "balance", "amount_residual", "reconciled",
                "matching_number", "full_reconcile_id",
            ],
            "limit": 50000,
            "order": "move_id,id",
        },
    )
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line.get("move_id")), []).append(line)

    rows = []
    for bank in bank_lines:
        for line in by_move.get(rel_id(bank.get("move_id")), []):
            rows.append({
                "Bank Statement Line ID": bank["id"],
                "Bank Date": bank.get("date", ""),
                "Bank Amount": bank.get("amount", 0),
                "Bank Ref": bank.get("payment_ref", ""),
                "Bank Partner": rel_name(bank.get("partner_id")),
                "Bank Is Reconciled": bank.get("is_reconciled", False),
                "Bank Move": rel_name(bank.get("move_id")),
                "Move Line ID": line["id"],
                "Move Line Date": line.get("date", ""),
                "Move Line Name": line.get("name", ""),
                "Move Line Ref": line.get("ref", ""),
                "Move Line Partner": rel_name(line.get("partner_id")),
                "Account": rel_name(line.get("account_id")),
                "Debit": line.get("debit", 0),
                "Credit": line.get("credit", 0),
                "Balance": line.get("balance", 0),
                "Residual": line.get("amount_residual", 0),
                "Move Line Reconciled": line.get("reconciled", False),
                "Matching Number": line.get("matching_number", ""),
                "Full Reconcile": rel_name(line.get("full_reconcile_id")),
            })

    fields = [
        "Bank Statement Line ID", "Bank Date", "Bank Amount", "Bank Ref", "Bank Partner",
        "Bank Is Reconciled", "Bank Move", "Move Line ID", "Move Line Date", "Move Line Name",
        "Move Line Ref", "Move Line Partner", "Account", "Debit", "Credit", "Balance",
        "Residual", "Move Line Reconciled", "Matching Number", "Full Reconcile",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Connected uid: {uid}")
    print(f"Bank lines: {len(bank_lines)}")
    print(f"Snapshot rows: {len(rows)}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
