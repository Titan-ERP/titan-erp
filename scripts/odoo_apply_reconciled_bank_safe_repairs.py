import argparse
import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
ACCOUNTING = ROOT / "odoo_imports" / "accounting"
PLAN = ACCOUNTING / "reconciled_bank_matching_repair_plan_2026.csv"
OUT = ACCOUNTING / "reconciled_bank_matching_safe_repair_results_2026.csv"
COMPANY = "Southern Equipment Company (Laurel)"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def rel_id(value):
    return value[0] if isinstance(value, list) and value else False


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def account_domain(models, db, uid, api_key, company_id, account_name):
    fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    domain = [("name", "=", account_name)]
    if "company_ids" in fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in fields:
        domain.append(("company_id", "=", company_id))
    return domain


def get_company(models, db, uid, api_key):
    rows = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})
    if not rows:
        raise SystemExit(f"Company not found: {COMPANY}")
    return rows[0]


def load_accounts(models, db, uid, api_key, company_id, names):
    accounts = {}
    for name in sorted({name for name in names if name}):
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "account.account",
            "search_read",
            [account_domain(models, db, uid, api_key, company_id, name)],
            {"fields": ["id", "name"], "limit": 2},
        )
        if len(rows) != 1:
            raise SystemExit(f"Expected one account named {name}; found {len(rows)}")
        accounts[name] = rows[0]["id"]
    return accounts


def one_int(value):
    values = [part.strip() for part in str(value or "").split(";") if part.strip()]
    if len(values) != 1 or not values[0].isdigit():
        return None
    return int(values[0])


def apply_account_recode(models, db, uid, api_key, row, accounts, apply):
    move_line_id = one_int(row["Counterpart Move Line IDs"])
    if not move_line_id:
        return {**row, "Status": "Review", "Applied": False, "Result": "Expected exactly one counterpart move line id."}
    before = execute(
        models, db, uid, api_key, "account.move.line", "read", [[move_line_id]],
        {"fields": ["id", "account_id", "partner_id"]},
    )[0]
    target_account_id = accounts[row["Target Account"]]
    if apply:
        execute(models, db, uid, api_key, "account.move.line", "write", [[move_line_id], {"account_id": target_account_id}])
    after = execute(
        models, db, uid, api_key, "account.move.line", "read", [[move_line_id]],
        {"fields": ["id", "account_id", "partner_id"]},
    )[0]
    return {
        **row,
        "Status": "Applied" if apply and rel_id(after["account_id"]) == target_account_id else ("Ready" if not apply else "Review"),
        "Applied": apply and rel_id(after["account_id"]) == target_account_id,
        "Before Account": rel_name(before.get("account_id")),
        "After Account": rel_name(after.get("account_id")),
        "Before Partner": rel_name(before.get("partner_id")),
        "After Partner": rel_name(after.get("partner_id")),
        "Result": "Account recode applied." if apply else "Would recode counterpart move line account.",
    }


def apply_partner(models, db, uid, api_key, row, apply):
    partner_id = int(row["Target Partner ID"])
    bank_id = int(row["Bank Statement Line ID"])
    move_line_id = one_int(row["Counterpart Move Line IDs"])
    if not move_line_id:
        return {**row, "Status": "Review", "Applied": False, "Result": "Expected exactly one counterpart move line id."}
    bank_before = execute(models, db, uid, api_key, "account.bank.statement.line", "read", [[bank_id]], {"fields": ["partner_id"]})[0]
    line_before = execute(models, db, uid, api_key, "account.move.line", "read", [[move_line_id]], {"fields": ["partner_id"]})[0]
    if apply:
        execute(models, db, uid, api_key, "account.bank.statement.line", "write", [[bank_id], {"partner_id": partner_id}])
        execute(models, db, uid, api_key, "account.move.line", "write", [[move_line_id], {"partner_id": partner_id}])
    bank_after = execute(models, db, uid, api_key, "account.bank.statement.line", "read", [[bank_id]], {"fields": ["partner_id"]})[0]
    line_after = execute(models, db, uid, api_key, "account.move.line", "read", [[move_line_id]], {"fields": ["partner_id"]})[0]
    ok = rel_id(bank_after.get("partner_id")) == partner_id and rel_id(line_after.get("partner_id")) == partner_id
    return {
        **row,
        "Status": "Applied" if apply and ok else ("Ready" if not apply else "Review"),
        "Applied": apply and ok,
        "Before Bank Partner": rel_name(bank_before.get("partner_id")),
        "After Bank Partner": rel_name(bank_after.get("partner_id")),
        "Before Partner": rel_name(line_before.get("partner_id")),
        "After Partner": rel_name(line_after.get("partner_id")),
        "Result": "Partner applied to bank line and counterpart move line." if apply else "Would set partner on bank line and counterpart move line.",
    }


def main():
    parser = argparse.ArgumentParser(description="Apply safe repairs from the reconciled bank repair plan.")
    parser.add_argument("--apply", action="store_true", help="Apply safe account recodes and exact partner updates. Default is dry run.")
    args = parser.parse_args()

    plan = read_csv(PLAN)
    selected = [
        row for row in plan
        if row["Action"] in {"Safe account recode candidate", "Set bank-line partner candidate"}
        and row["Confidence"] == "High"
    ]
    db, uid, api_key, models = connect()
    company = get_company(models, db, uid, api_key)
    accounts = load_accounts(models, db, uid, api_key, company["id"], [row["Target Account"] for row in selected])
    results = []
    for row in selected:
        try:
            if row["Action"] == "Safe account recode candidate":
                results.append(apply_account_recode(models, db, uid, api_key, row, accounts, args.apply))
            elif row["Action"] == "Set bank-line partner candidate":
                results.append(apply_partner(models, db, uid, api_key, row, args.apply))
        except Exception as exc:
            results.append({**row, "Status": "Error", "Applied": False, "Result": str(exc)})
    fields = list(plan[0].keys()) + [
        "Status",
        "Applied",
        "Before Account",
        "After Account",
        "Before Bank Partner",
        "After Bank Partner",
        "Before Partner",
        "After Partner",
        "Result",
    ]
    write_csv(OUT, results, fields)
    print(f"Connected uid: {uid}")
    print(f"Applied: {args.apply}")
    print(f"Selected safe repairs: {len(selected)}")
    print(f"Applied count: {sum(1 for row in results if str(row.get('Applied')).lower() == 'true')}")
    print(f"Output: {OUT}")


if __name__ == "__main__":
    main()
