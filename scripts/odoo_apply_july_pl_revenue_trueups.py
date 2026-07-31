import csv
import os
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "july_pl_revenue_trueup_results.csv"

COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Miscellaneous Operations"

ACCOUNTS = {
    "parts": "Parts Revenue",
    "service": "Service Revenue",
    "rental": "Rental Revenue",
    "tax": "Sales Tax Payable",
}

TRUEUPS = [
    {
        "date": "2026-07-31",
        "ref": "July P&L revenue true-up Shop Boss RO 1106; RO 1107",
        "source": "Shop Boss closed ROs missing from Service Revenue",
        "parts_debit": "485.00",
        "service_credit": "477.84",
        "rental_credit": "0.00",
        "tax_credit": "7.16",
    },
    {
        "date": "2026-07-31",
        "ref": "July P&L rental true-up TX18; TX10; U35",
        "source": "Confirmed rental products from July canceled invoice evidence",
        "parts_debit": "3809.45",
        "service_credit": "0.00",
        "rental_credit": "3619.50",
        "tax_credit": "189.95",
    },
    {
        "date": "2026-07-31",
        "ref": "July P&L service true-up Shop Boss payments received",
        "source": "Shop Boss July payments on ROs closed before or during July",
        "parts_debit": "4053.37",
        "service_credit": "4053.37",
        "rental_credit": "0.00",
        "tax_credit": "0.00",
    },
]


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read(models, db, uid, api_key, model, domain, fields, limit=1000):
    return execute(models, db, uid, api_key, model, "search_read", [domain], {"fields": fields, "limit": limit})


def single(models, db, uid, api_key, model, domain, fields, label):
    rows = read(models, db, uid, api_key, model, domain, fields, limit=2)
    if len(rows) != 1:
        raise SystemExit(f"Expected one {label}; found {len(rows)}")
    return rows[0]


def account_domain(name, company_id):
    return [("name", "=", name), ("company_ids", "in", [company_id])]


def write_results(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "status",
        "date",
        "ref",
        "source",
        "parts_debit",
        "service_credit",
        "rental_credit",
        "tax_credit",
        "odoo_move",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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

    company = single(models, db, uid, api_key, "res.company", [("name", "=", COMPANY)], ["id"], COMPANY)
    journal = single(
        models,
        db,
        uid,
        api_key,
        "account.journal",
        [("name", "=", JOURNAL), ("company_id", "=", company["id"])],
        ["id"],
        JOURNAL,
    )
    accounts = {
        key: single(models, db, uid, api_key, "account.account", account_domain(name, company["id"]), ["id", "name"], name)
        for key, name in ACCOUNTS.items()
    }

    rows = []
    for item in TRUEUPS:
        row = {**item, "status": "", "odoo_move": ""}
        existing = read(models, db, uid, api_key, "account.move", [("company_id", "=", company["id"]), ("ref", "=", item["ref"])], ["name"], limit=5)
        if existing:
            row["status"] = "Skipped"
            row["odoo_move"] = existing[0]["name"]
            rows.append(row)
            continue
        parts_debit = money(item["parts_debit"])
        service_credit = money(item["service_credit"])
        rental_credit = money(item["rental_credit"])
        tax_credit = money(item["tax_credit"])
        if parts_debit != service_credit + rental_credit + tax_credit:
            raise SystemExit(f"Unbalanced true-up: {item['ref']}")
        line_ids = [
            (0, 0, {"account_id": accounts["parts"]["id"], "name": item["source"], "debit": float(parts_debit), "credit": 0.0}),
        ]
        if service_credit:
            line_ids.append((0, 0, {"account_id": accounts["service"]["id"], "name": item["source"], "debit": 0.0, "credit": float(service_credit)}))
        if rental_credit:
            line_ids.append((0, 0, {"account_id": accounts["rental"]["id"], "name": item["source"], "debit": 0.0, "credit": float(rental_credit)}))
        if tax_credit:
            line_ids.append((0, 0, {"account_id": accounts["tax"]["id"], "name": item["source"], "debit": 0.0, "credit": float(tax_credit)}))
        move_id = execute(
            models,
            db,
            uid,
            api_key,
            "account.move",
            "create",
            [{
                "company_id": company["id"],
                "journal_id": journal["id"],
                "date": item["date"],
                "ref": item["ref"],
                "line_ids": line_ids,
            }],
        )
        execute(models, db, uid, api_key, "account.move", "action_post", [[move_id]])
        move = read(models, db, uid, api_key, "account.move", [("id", "=", move_id)], ["name"], limit=1)[0]
        row["status"] = "Applied"
        row["odoo_move"] = move["name"]
        rows.append(row)
    write_results(rows)
    for row in rows:
        print(row)
    print(f"Results: {OUT}")


if __name__ == "__main__":
    main()
