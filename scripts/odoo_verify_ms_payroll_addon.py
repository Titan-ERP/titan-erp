import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"

MODULE_NAME = "l10n_us_hr_payroll_ms_status"
EXPECTED_STATUSES = {
    "ms_single": "MS: Single",
    "ms_head_of_family": "MS: Head-of-Family",
    "ms_married_spouse_not_employed": "MS: Married (Spouse Not Employed)",
    "ms_married_both_spouses_employed": "MS: Married (Both Spouses Employed)",
}


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

    failures = []

    modules = execute(
        models,
        db,
        uid,
        api_key,
        "ir.module.module",
        "search_read",
        [[("name", "=", MODULE_NAME)]],
        {"fields": ["name", "state", "latest_version"], "limit": 1},
    )
    module_state = modules[0]["state"] if modules else "not found"
    if module_state != "installed":
        failures.append(f"Module {MODULE_NAME} is {module_state}.")

    employee_fields = execute(
        models,
        db,
        uid,
        api_key,
        "hr.version",
        "fields_get",
        [],
        {"attributes": ["selection"]},
    )
    selection = dict(employee_fields["l10n_us_state_filing_status"].get("selection") or [])
    missing_statuses = {
        key: label
        for key, label in EXPECTED_STATUSES.items()
        if selection.get(key) != label
    }
    if missing_statuses:
        failures.append(f"Missing or mismatched Mississippi statuses: {missing_statuses}")

    rules = execute(
        models,
        db,
        uid,
        api_key,
        "hr.salary.rule",
        "search_read",
        [[("code", "=", "MSINCOMETAX")]],
        {
            "fields": [
                "name",
                "code",
                "active",
                "sequence",
                "struct_id",
                "category_id",
                "condition_select",
                "amount_select",
                "appears_on_payslip",
            ],
            "limit": 5,
        },
    )
    if len(rules) != 1:
        failures.append(f"Expected exactly one MSINCOMETAX salary rule; found {len(rules)}.")
    else:
        rule = rules[0]
        expected_rule_values = {
            "active": True,
            "condition_select": "python",
            "amount_select": "code",
            "appears_on_payslip": True,
        }
        for field, expected in expected_rule_values.items():
            if rule.get(field) != expected:
                failures.append(f"MSINCOMETAX {field} is {rule.get(field)!r}, expected {expected!r}.")

    taxable_categories = execute(
        models,
        db,
        uid,
        api_key,
        "hr.salary.rule.category",
        "search_read",
        [[("code", "=", "TAXABLE")]],
        {"fields": ["name", "code"], "limit": 5},
    )
    if not taxable_categories:
        failures.append("No hr.salary.rule.category with code TAXABLE found.")

    liability_accounts = execute(
        models,
        db,
        uid,
        api_key,
        "account.account",
        "search_read",
        [[("code", "=", "230100")]],
        {"fields": ["code", "name", "account_type"], "limit": 5},
    )
    if not liability_accounts:
        failures.append("No account.account with code 230100 found.")

    print(f"Connected uid: {uid}")
    print(f"Module state: {module_state}")
    print("Mississippi statuses:")
    for key, label in EXPECTED_STATUSES.items():
        print(f"- {key}: {selection.get(key, 'missing')}")
    print("MSINCOMETAX salary rule:")
    for rule in rules:
        print(
            f"- {rule['name']} | active={rule['active']} | sequence={rule['sequence']} | "
            f"structure={rel_name(rule.get('struct_id'))} | category={rel_name(rule.get('category_id'))}"
        )
    print("TAXABLE categories:")
    for category in taxable_categories:
        print(f"- {category['code']} {category['name']}")
    print("Account 230100:")
    for account in liability_accounts:
        print(f"- {account['code']} {account['name']} ({account.get('account_type') or 'no type'})")

    if failures:
        print("\nVerification failures:")
        for failure in failures:
            print(f"- {failure}")
        raise SystemExit(1)

    print("\nMississippi payroll addon verification passed.")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.ProtocolError as exc:
        print(f"Odoo XML-RPC protocol error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
