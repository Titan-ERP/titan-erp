import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "july_bank_unreconcile_results.csv"
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


def execute_void_ok(models, db, uid, api_key, model, method, args, kwargs=None):
    try:
        return execute(models, db, uid, api_key, model, method, args, kwargs)
    except xmlrpc.client.Fault as exc:
        if "cannot marshal None unless allow_none is enabled" in str(exc):
            return None
        raise


def rel(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def write_csv(rows):
    fields = ["Status", "Bank Statement Line ID", "Date", "Amount", "Payment Ref", "Before Reconciled", "After Reconciled", "Method", "Reason"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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

    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})[0]
    journal = execute(
        models, db, uid, api_key, "account.journal", "search_read",
        [[("name", "=", JOURNAL), ("company_id", "=", company["id"])]],
        {"fields": ["id"], "limit": 1},
    )[0]
    lines = execute(
        models, db, uid, api_key, "account.bank.statement.line", "search_read",
        [[
            ("company_id", "=", company["id"]),
            ("journal_id", "=", journal["id"]),
            ("date", ">=", "2026-07-01"),
            ("date", "<", "2026-08-01"),
            ("is_reconciled", "=", True),
        ]],
        {"fields": ["id", "date", "amount", "payment_ref", "is_reconciled"], "limit": 20000, "order": "date asc,id asc"},
    )
    results = []
    if not apply:
        for line in lines:
            results.append({
                "Status": "Ready",
                "Bank Statement Line ID": line["id"],
                "Date": line.get("date", ""),
                "Amount": line.get("amount", 0),
                "Payment Ref": line.get("payment_ref", ""),
                "Before Reconciled": line.get("is_reconciled"),
                "After Reconciled": "",
                "Method": "button_undo_reconciliation",
                "Reason": "Would undo bank statement line reconciliation.",
            })
        write_csv(results)
    else:
        ids = [line["id"] for line in lines]
        method = "button_undo_reconciliation"
        try:
            execute_void_ok(models, db, uid, api_key, "account.bank.statement.line", method, [ids])
        except Exception as exc:
            method = "action_undo_reconciliation"
            execute_void_ok(models, db, uid, api_key, "account.bank.statement.line", method, [ids])
        after = execute(
            models, db, uid, api_key, "account.bank.statement.line", "read",
            [ids],
            {"fields": ["id", "is_reconciled"]},
        )
        after_by_id = {row["id"]: row for row in after}
        for line in lines:
            after_reconciled = after_by_id[line["id"]]["is_reconciled"]
            results.append({
                "Status": "Unreconciled" if not after_reconciled else "Review",
                "Bank Statement Line ID": line["id"],
                "Date": line.get("date", ""),
                "Amount": line.get("amount", 0),
                "Payment Ref": line.get("payment_ref", ""),
                "Before Reconciled": line.get("is_reconciled"),
                "After Reconciled": after_reconciled,
                "Method": method,
                "Reason": "Undo reconciliation completed." if not after_reconciled else "Line is still reconciled after undo attempt.",
            })
        write_csv(results)

    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for row in results[:20]:
        print(row)


if __name__ == "__main__":
    main()
