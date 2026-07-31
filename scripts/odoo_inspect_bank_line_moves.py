import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env(ENV_PATH)
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def main():
    db, uid, api_key, models = connect()
    for reconciled in [True, False]:
        lines = execute(
            models,
            db,
            uid,
            api_key,
            "account.bank.statement.line",
            "search_read",
            [[("date", ">=", "2026-04-01"), ("date", "<=", "2026-06-30"), ("is_reconciled", "=", reconciled)]],
            {"fields": ["id", "date", "payment_ref", "amount", "is_reconciled", "move_id"], "limit": 5, "order": "date asc"},
        )
        print(f"\n=== is_reconciled={reconciled} ===")
        for line in lines:
            move_id = line["move_id"][0]
            move_lines = execute(
                models,
                db,
                uid,
                api_key,
                "account.move.line",
                "search_read",
                [[("move_id", "=", move_id)]],
                {"fields": ["id", "name", "account_id", "partner_id", "debit", "credit", "balance", "amount_residual", "reconciled"], "limit": 20, "order": "id asc"},
            )
            print(f"\nBSL {line['id']} {line['date']} amount={line['amount']} ref={line['payment_ref']} move={line['move_id'][1]}")
            for ml in move_lines:
                account = ml["account_id"][1] if ml.get("account_id") else ""
                partner = ml["partner_id"][1] if ml.get("partner_id") else ""
                print(f"  ML {ml['id']} acct={account} partner={partner} debit={ml['debit']} credit={ml['credit']} bal={ml['balance']} residual={ml['amount_residual']} rec={ml['reconciled']}")


if __name__ == "__main__":
    main()
