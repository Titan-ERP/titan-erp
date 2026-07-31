import argparse
import csv
import os
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "accounting"
APPLIED_RESULTS_PATH = OUT_DIR / "parts_category_account_fix_applied_results.csv"
DRY_RUN_RESULTS_PATH = OUT_DIR / "parts_category_account_fix_dry_run_results.csv"

PARTS_PARENT = "Parts"
TARGET_COMPANY = "Southern Equipment Company (Laurel)"
TARGET_INCOME_NAME = "Parts Revenue"
TARGET_EXPENSE_NAME = "Parts COGS"


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
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def rel_id(value):
    if isinstance(value, list) and value:
        return value[0]
    return False


def rel_name(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def get_company(models, db, uid, api_key):
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "res.company",
        "search_read",
        [[("name", "=", TARGET_COMPANY)]],
        {"fields": ["id", "name"], "limit": 2},
    )
    if len(rows) != 1:
        raise SystemExit(f"Expected exactly one company named {TARGET_COMPANY}, found {len(rows)}.")
    return rows[0]


def get_account_by_name(models, db, uid, api_key, name, company_id):
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "account.account",
        "search_read",
        [[("name", "=", name), ("company_ids", "in", [company_id])]],
        {"fields": ["id", "code", "name", "company_ids"], "limit": 5},
    )
    if len(rows) != 1:
        raise SystemExit(f"Expected exactly one {TARGET_COMPANY} account named {name}, found {len(rows)}.")
    return rows[0]


def write_results(rows, path):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "timestamp",
        "mode",
        "category_id",
        "complete_name",
        "old_income",
        "new_income",
        "old_expense",
        "new_expense",
        "changed",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Set Parts child category accounting to Parts income/expense accounts.")
    parser.add_argument("--apply", action="store_true", help="Write changes to Odoo. Without this, runs read-only.")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    company = get_company(models, db, uid, api_key)
    income = get_account_by_name(models, db, uid, api_key, TARGET_INCOME_NAME, company["id"])
    expense = get_account_by_name(models, db, uid, api_key, TARGET_EXPENSE_NAME, company["id"])

    categories = execute(
        models,
        db,
        uid,
        api_key,
        "product.category",
        "search_read",
        [[("complete_name", "=ilike", f"{PARTS_PARENT} /%")]],
        {
            "fields": [
                "id",
                "complete_name",
                "property_account_income_categ_id",
                "property_account_expense_categ_id",
            ],
            "limit": 500,
            "order": "complete_name asc",
        },
    )

    timestamp = datetime.now().isoformat(timespec="seconds")
    results = []
    changed_count = 0
    for category in categories:
        old_income_id = rel_id(category.get("property_account_income_categ_id"))
        old_expense_id = rel_id(category.get("property_account_expense_categ_id"))
        needs_change = old_income_id != income["id"] or old_expense_id != expense["id"]
        if args.apply and needs_change:
            execute(
                models,
                db,
                uid,
                api_key,
                "product.category",
                "write",
                [
                    [category["id"]],
                    {
                        "property_account_income_categ_id": income["id"],
                        "property_account_expense_categ_id": expense["id"],
                    },
                ],
            )
        if needs_change:
            changed_count += 1
        results.append(
            {
                "timestamp": timestamp,
                "mode": "applied" if args.apply else "dry_run",
                "category_id": category["id"],
                "complete_name": category["complete_name"],
                "old_income": rel_name(category.get("property_account_income_categ_id")),
                "new_income": f"{income['code']} {income['name']}",
                "old_expense": rel_name(category.get("property_account_expense_categ_id")),
                "new_expense": f"{expense['code']} {expense['name']}",
                "changed": "yes" if needs_change else "no",
            }
        )

    results_path = APPLIED_RESULTS_PATH if args.apply else DRY_RUN_RESULTS_PATH
    write_results(results, results_path)
    print(f"Connected uid: {uid}")
    print(f"Database: {db}")
    print(f"Company: {company['name']}")
    print(f"Mode: {'applied' if args.apply else 'dry_run'}")
    print(f"Parts child categories checked: {len(categories)}")
    print(f"Categories needing change: {changed_count}")
    print(f"Results: {results_path}")


if __name__ == "__main__":
    main()
