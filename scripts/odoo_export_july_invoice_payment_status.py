import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "odoo_july_invoice_payment_export.csv"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def display(value):
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return value[1]
    if value is False or value is None:
        return ""
    return value


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

    company = execute(
        models, db, uid, api_key, "res.company", "search_read",
        [[("name", "=", "Southern Equipment Company (Laurel)")]],
        {"fields": ["id"], "limit": 1},
    )[0]
    fields = [
        "id", "name", "state", "payment_state", "partner_id", "invoice_date",
        "amount_untaxed", "amount_tax", "amount_total", "amount_residual",
        "invoice_origin", "ref", "move_type", "reversal_move_ids",
    ]
    rows = execute(
        models, db, uid, api_key, "account.move", "search_read",
        [[
            ("company_id", "=", company["id"]),
            ("move_type", "in", ["out_invoice", "out_refund"]),
            ("invoice_date", ">=", "2026-07-01"),
            ("invoice_date", "<=", "2026-07-31"),
        ]],
        {"fields": fields, "order": "invoice_date,id"},
    )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                **row,
                "partner_id": display(row.get("partner_id")),
                "reversal_move_ids": ",".join(str(item) for item in row.get("reversal_move_ids") or []),
            })

    print(f"Connected uid: {uid}")
    print(f"Rows: {len(rows)}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
