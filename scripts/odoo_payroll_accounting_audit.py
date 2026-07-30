import csv
import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "payroll_salary_rule_account_audit.csv"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def rel_name(value):
    if isinstance(value, list) and len(value) > 1:
        return value[1]
    return ""


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

    fields_meta = execute(models, db, uid, api_key, "hr.salary.rule", "fields_get", [], {"attributes": ["type"]})
    fields = ["id", "name", "code", "sequence", "active", "category_id", "struct_id"]
    for candidate in ["account_debit", "account_credit", "account_debit_id", "account_credit_id"]:
        if candidate in fields_meta:
            fields.append(candidate)
    for candidate in ["appears_on_payslip", "condition_select", "amount_select"]:
        if candidate in fields_meta:
            fields.append(candidate)

    rules = execute(
        models,
        db,
        uid,
        api_key,
        "hr.salary.rule",
        "search_read",
        [[]],
        {"fields": fields, "limit": 1000, "order": "sequence asc, code asc"},
    )

    debit_field = "account_debit_id" if "account_debit_id" in fields else "account_debit"
    credit_field = "account_credit_id" if "account_credit_id" in fields else "account_credit"
    rows = []
    for rule in rules:
        debit = rel_name(rule.get(debit_field))
        credit = rel_name(rule.get(credit_field))
        has_account = bool(debit or credit)
        appears = rule.get("appears_on_payslip")
        rows.append(
            {
                "Rule ID": rule.get("id"),
                "Sequence": rule.get("sequence"),
                "Code": rule.get("code"),
                "Name": rule.get("name"),
                "Active": rule.get("active"),
                "Structure": rel_name(rule.get("struct_id")),
                "Category": rel_name(rule.get("category_id")),
                "Appears on Payslip": appears if appears is not None else "",
                "Amount Type": rule.get("amount_select", ""),
                "Condition Type": rule.get("condition_select", ""),
                "Debit Account": debit,
                "Credit Account": credit,
                "Has Accounting": "yes" if has_account else "no",
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "Rule ID",
            "Sequence",
            "Code",
            "Name",
            "Active",
            "Structure",
            "Category",
            "Appears on Payslip",
            "Amount Type",
            "Condition Type",
            "Debit Account",
            "Credit Account",
            "Has Accounting",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    active = [row for row in rows if row["Active"]]
    active_with_accounts = [row for row in active if row["Has Accounting"] == "yes"]
    active_without_accounts = [row for row in active if row["Has Accounting"] == "no"]
    by_structure = {}
    for row in rows:
        key = row["Structure"] or "No structure"
        total, with_accounts = by_structure.get(key, (0, 0))
        by_structure[key] = (total + 1, with_accounts + (1 if row["Has Accounting"] == "yes" else 0))

    print(f"Connected uid: {uid}")
    print(f"Salary rules exported: {len(rows)}")
    print(f"Active salary rules: {len(active)}")
    print(f"Active salary rules with accounting: {len(active_with_accounts)}")
    print(f"Active salary rules without accounting: {len(active_without_accounts)}")
    print("Accounting by structure:")
    for structure, (total, with_accounts) in sorted(by_structure.items()):
        print(f"- {structure}: {with_accounts} of {total}")
    print(f"Report: {OUT}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
