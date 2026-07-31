import csv
import os
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
COMPARISON = ROOT / "odoo_imports" / "accounting" / "service_revenue_ytd_shop_boss_vs_odoo.csv"
OUT = ROOT / "odoo_imports" / "accounting" / "service_revenue_ytd_trueup_results.csv"

COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Miscellaneous Operations"
PARTS_ACCOUNT = "Parts Revenue"
SERVICE_ACCOUNT = "Service Revenue"

MONTH_END = {
    "2026-02": "2026-02-28",
    "2026-03": "2026-03-31",
    "2026-04": "2026-04-30",
    "2026-05": "2026-05-31",
    "2026-06": "2026-06-30",
    "2026-07": "2026-07-31",
}


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


def write_results(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["status", "month", "date", "ref", "shop_boss_service", "odoo_service_before", "parts_debit", "service_credit", "odoo_move"]
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
    journal = single(models, db, uid, api_key, "account.journal", [("name", "=", JOURNAL), ("company_id", "=", company["id"])], ["id"], JOURNAL)
    parts = single(models, db, uid, api_key, "account.account", [("name", "=", PARTS_ACCOUNT), ("company_ids", "in", [company["id"]])], ["id"], PARTS_ACCOUNT)
    service = single(models, db, uid, api_key, "account.account", [("name", "=", SERVICE_ACCOUNT), ("company_ids", "in", [company["id"]])], ["id"], SERVICE_ACCOUNT)

    rows = []
    with COMPARISON.open("r", newline="", encoding="utf-8-sig") as f:
        for item in csv.DictReader(f):
            month = item["month"]
            if month == "2026-07":
                # July was intentionally set from Shop Boss payments received, not closed-RO final date.
                continue
            shortfall = money(item["service_shortfall"])
            if shortfall <= 0:
                continue
            date = MONTH_END[month]
            ref = f"Shop Boss service revenue true-up {month}"
            row = {
                "status": "",
                "month": month,
                "date": date,
                "ref": ref,
                "shop_boss_service": money(item["shop_boss_service"]),
                "odoo_service_before": money(item["odoo_service"]),
                "parts_debit": shortfall,
                "service_credit": shortfall,
                "odoo_move": "",
            }
            existing = read(models, db, uid, api_key, "account.move", [("company_id", "=", company["id"]), ("ref", "=", ref)], ["name"], limit=5)
            if existing:
                row["status"] = "Skipped"
                row["odoo_move"] = existing[0]["name"]
                rows.append(row)
                continue
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
                    "date": date,
                    "ref": ref,
                    "line_ids": [
                        (0, 0, {"account_id": parts["id"], "name": ref, "debit": float(shortfall), "credit": 0.0}),
                        (0, 0, {"account_id": service["id"], "name": ref, "debit": 0.0, "credit": float(shortfall)}),
                    ],
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
