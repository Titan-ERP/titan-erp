import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def model_exists(models, db, uid, api_key, model):
    return bool(execute(models, db, uid, api_key, "ir.model", "search_count", [[("model", "=", model)]]))


def count_if_available(models, db, uid, api_key, model, domain=None):
    if not model_exists(models, db, uid, api_key, model):
        return "not installed"
    try:
        return execute(models, db, uid, api_key, model, "search_count", [domain or []])
    except xmlrpc.client.Fault as exc:
        return f"not readable: {exc.faultString}"


def read_if_available(models, db, uid, api_key, model, domain, fields, limit=20, order=None):
    if not model_exists(models, db, uid, api_key, model):
        return None
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    try:
        return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)
    except xmlrpc.client.Fault as exc:
        return f"not readable: {exc.faultString}"


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

    modules = execute(
        models,
        db,
        uid,
        api_key,
        "ir.module.module",
        "search_read",
        [[
            "|",
            ("name", "ilike", "payroll"),
            ("shortdesc", "ilike", "payroll"),
        ]],
        {
            "fields": ["name", "shortdesc", "state", "latest_version"],
            "limit": 100,
            "order": "name asc",
        },
    )
    contract_modules = execute(
        models,
        db,
        uid,
        api_key,
        "ir.module.module",
        "search_read",
        [[
            "|",
            ("name", "ilike", "contract"),
            ("shortdesc", "ilike", "contract"),
        ]],
        {
            "fields": ["name", "shortdesc", "state", "latest_version"],
            "limit": 100,
            "order": "name asc",
        },
    )

    print(f"Connected uid: {uid}")
    user = execute(models, db, uid, api_key, "res.users", "read", [[uid]], {"fields": ["name", "login"]})[0]
    print(f"User: {user.get('name')} <{user.get('login')}>")
    group_xmlids = [
        "hr_payroll.group_hr_payroll_user",
        "hr_payroll.group_hr_payroll_manager",
        "hr.group_hr_user",
        "hr.group_hr_manager",
        "base.group_system",
    ]
    print("Selected security group checks:")
    for xmlid in group_xmlids:
        try:
            has_group = execute(models, db, uid, api_key, "res.users", "has_group", [[uid], xmlid])
        except xmlrpc.client.Fault as exc:
            has_group = f"not checkable: {exc.faultString}"
        print(f"- {xmlid}: {has_group}")

    print("Payroll-related modules:")
    important_module_names = {
        "hr_payroll",
        "hr_payroll_account",
        "l10n_us_hr_payroll",
        "l10n_us_hr_payroll_account",
        "l10n_us_hr_payroll_adp",
        "documents_hr_payroll",
        "spreadsheet_dashboard_hr_payroll",
    }
    important_modules = [module for module in modules if module.get("name") in important_module_names]
    if important_modules:
        for module in important_modules:
            version = module.get("latest_version") or ""
            print(f"- {module['name']}: {module.get('shortdesc')} [{module.get('state')}] {version}")
    else:
        print("- none found")

    print("Contract-related HR/payroll modules:")
    relevant_contract_modules = []
    for module in contract_modules:
        text = f"{module.get('name')} {module.get('shortdesc')}".lower()
        if any(term in text for term in ["hr", "employee", "payroll", "contract"]):
            relevant_contract_modules.append(module)
    if relevant_contract_modules:
        for module in relevant_contract_modules:
            version = module.get("latest_version") or ""
            print(f"- {module['name']}: {module.get('shortdesc')} [{module.get('state')}] {version}")
    else:
        print("- none found")

    checks = [
        ("hr.employee", []),
        ("hr.contract", []),
        ("hr.payslip", []),
        ("hr.payslip.run", []),
        ("hr.salary.rule", []),
        ("hr.payroll.structure", []),
        ("hr.work.entry", [("state", "=", "conflict")]),
    ]
    print("Payroll/HR model counts:")
    for model, domain in checks:
        label = model if not domain else f"{model} {domain}"
        print(f"- {label}: {count_if_available(models, db, uid, api_key, model, domain)}")

    employee_fields = execute(models, db, uid, api_key, "hr.employee", "fields_get", [], {"attributes": ["type"]})
    employee_report_fields = ["name"]
    for field in ["company_id", "department_id", "job_id", "work_email"]:
        if field in employee_fields:
            employee_report_fields.append(field)
    employees = read_if_available(models, db, uid, api_key, "hr.employee", [], employee_report_fields, limit=50, order="name asc")
    if isinstance(employees, list):
        by_company = {}
        for employee in employees:
            company = rel_name(employee.get("company_id")) or "No company"
            by_company[company] = by_company.get(company, 0) + 1
        print("Employees by company:")
        for company, count in sorted(by_company.items()):
            print(f"- {company}: {count}")

    structures = read_if_available(
        models,
        db,
        uid,
        api_key,
        "hr.payroll.structure",
        [],
        ["name", "country_id", "type_id"],
        limit=20,
        order="name asc",
    )
    print("Payroll structures:")
    if isinstance(structures, list) and structures:
        for structure in structures:
            country = rel_name(structure.get("country_id")) or "No country"
            structure_type = rel_name(structure.get("type_id")) or "No type"
            print(f"- {structure.get('name')} ({country}; {structure_type})")
    elif isinstance(structures, str):
        print(f"- {structures}")
    else:
        print("- none")

    salary_rule_fields = execute(models, db, uid, api_key, "hr.salary.rule", "fields_get", [], {"attributes": ["type"]})
    rule_fields = ["name", "code", "active"]
    for field in ["account_debit", "account_credit", "account_debit_id", "account_credit_id"]:
        if field in salary_rule_fields:
            rule_fields.append(field)
    rules = read_if_available(models, db, uid, api_key, "hr.salary.rule", [], rule_fields, limit=500, order="sequence asc")
    if isinstance(rules, list):
        debit_field = "account_debit_id" if "account_debit_id" in rule_fields else "account_debit"
        credit_field = "account_credit_id" if "account_credit_id" in rule_fields else "account_credit"
        rules_with_accounts = [
            rule for rule in rules
            if rel_name(rule.get(debit_field)) or rel_name(rule.get(credit_field))
        ]
        print(f"Salary rules with accounting set: {len(rules_with_accounts)} of {len(rules)}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
