import csv
import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "accounting"
SUMMARY = OUT_DIR / "payroll_readiness_summary.md"
EMPLOYEE_REPORT = OUT_DIR / "payroll_employee_readiness.csv"
MODEL_REPORT = OUT_DIR / "payroll_model_field_audit.csv"


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


def model_exists(models, db, uid, api_key, model):
    return bool(execute(models, db, uid, api_key, "ir.model", "search_count", [[("model", "=", model)]]))


def fields_get(models, db, uid, api_key, model):
    if not model_exists(models, db, uid, api_key, model):
        return {}
    return execute(models, db, uid, api_key, model, "fields_get", [], {"attributes": ["string", "type", "relation", "required"]})


def count(models, db, uid, api_key, model, domain=None):
    if not model_exists(models, db, uid, api_key, model):
        return None
    return execute(models, db, uid, api_key, model, "search_count", [domain or []])


def search_read(models, db, uid, api_key, model, domain, fields, limit=1000, order=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def add_if_present(target, fields_meta, field):
    if field in fields_meta:
        target.append(field)


def yes_no(value):
    return "yes" if value else "no"


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

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    model_names = [
        "hr.employee",
        "hr.contract",
        "hr.payslip",
        "hr.payslip.run",
        "hr.salary.rule",
        "hr.payroll.structure",
        "hr.work.entry",
        "hr.work.entry.type",
        "res.partner.bank",
    ]
    model_rows = []
    for model in model_names:
        meta = fields_get(models, db, uid, api_key, model)
        interesting = []
        for field, values in sorted(meta.items()):
            text = f"{field} {values.get('string')} {values.get('relation')}".lower()
            if any(term in text for term in ["pay", "wage", "salary", "bank", "contract", "work", "tax", "ssn", "withhold", "schedule"]):
                interesting.append(field)
        model_rows.append(
            {
                "Model": model,
                "Installed": yes_no(bool(meta)),
                "Record Count": "" if not meta else count(models, db, uid, api_key, model),
                "Interesting Fields": "; ".join(interesting[:80]),
            }
        )

    with MODEL_REPORT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["Model", "Installed", "Record Count", "Interesting Fields"])
        writer.writeheader()
        writer.writerows(model_rows)

    employee_meta = fields_get(models, db, uid, api_key, "hr.employee")
    employee_fields = ["id", "name", "active"]
    for field in [
        "company_id",
        "department_id",
        "job_id",
        "employee_type",
        "work_email",
        "work_phone",
        "resource_calendar_id",
        "address_home_id",
        "private_email",
        "bank_account_id",
        "primary_bank_account_id",
        "bank_account_ids",
        "country_id",
        "identification_id",
        "ssnid",
        "birthday",
        "wage",
        "hourly_wage",
        "wage_type",
        "schedule_pay",
        "structure_id",
        "structure_type_id",
        "work_entry_source",
        "l10n_us_filing_status",
        "l10n_us_state_filing_status",
        "l10n_us_worker_compensation_id",
    ]:
        add_if_present(employee_fields, employee_meta, field)
    employees = search_read(models, db, uid, api_key, "hr.employee", [], employee_fields, order="name asc")

    contract_meta = fields_get(models, db, uid, api_key, "hr.contract")
    contracts_by_employee = {}
    if contract_meta:
        contract_fields = ["id", "employee_id", "state"]
        for field in ["name", "company_id", "date_start", "date_end", "wage", "structure_type_id", "schedule_pay", "resource_calendar_id"]:
            add_if_present(contract_fields, contract_meta, field)
        contracts = search_read(models, db, uid, api_key, "hr.contract", [], contract_fields, order="employee_id asc, date_start desc")
        for contract in contracts:
            employee_id = contract.get("employee_id")
            if isinstance(employee_id, list) and employee_id:
                contracts_by_employee.setdefault(employee_id[0], []).append(contract)

    bank_account_ids = []
    for employee in employees:
        for field in ["bank_account_id", "primary_bank_account_id"]:
            bank_value = employee.get(field)
            if isinstance(bank_value, list) and bank_value:
                bank_account_ids.append(bank_value[0])
        bank_values = employee.get("bank_account_ids")
        if isinstance(bank_values, list):
            bank_account_ids.extend(bank_values)
    bank_by_id = {}
    if bank_account_ids:
        bank_rows = search_read(
            models,
            db,
            uid,
            api_key,
            "res.partner.bank",
            [("id", "in", bank_account_ids)],
            ["id", "acc_number", "bank_id", "partner_id"],
        )
        bank_by_id = {row["id"]: row for row in bank_rows}

    employee_rows = []
    for employee in employees:
        employee_id = employee["id"]
        employee_contracts = contracts_by_employee.get(employee_id, [])
        open_contracts = [contract for contract in employee_contracts if contract.get("state") in {"open", "close"}]
        bank_ids = []
        for field in ["bank_account_id", "primary_bank_account_id"]:
            bank_value = employee.get(field)
            if isinstance(bank_value, list) and bank_value:
                bank_ids.append(bank_value[0])
        bank_values = employee.get("bank_account_ids")
        if isinstance(bank_values, list):
            bank_ids.extend(bank_values)
        bank = next((bank_by_id.get(bank_id) for bank_id in bank_ids if bank_by_id.get(bank_id)), None)

        missing = []
        review = []
        is_person = bool(employee.get("work_email") or employee.get("private_email") or employee.get("job_id") or employee.get("ssnid"))
        name = (employee.get("name") or "").lower()
        if name in {"administrator", "service", "southern equipment co"}:
            review.append("likely non-payroll user/company placeholder")
        if not employee.get("work_email") and not employee.get("private_email"):
            missing.append("email")
        if "resource_calendar_id" in employee_meta and not employee.get("resource_calendar_id"):
            missing.append("working schedule")
        if "address_home_id" in employee_meta and not employee.get("address_home_id"):
            missing.append("private address/contact")
        if any(field in employee_meta for field in ["bank_account_id", "primary_bank_account_id", "bank_account_ids"]) and not bank:
            missing.append("bank account")
        if "identification_id" in employee_meta and not employee.get("identification_id"):
            missing.append("employee id/SSN field")
        if "ssnid" in employee_meta and not employee.get("ssnid"):
            missing.append("SSN")
        if any(field in employee_meta for field in ["wage", "hourly_wage"]) and not (employee.get("wage") or employee.get("hourly_wage")):
            missing.append("wage")
        if "wage_type" in employee_meta and not employee.get("wage_type"):
            missing.append("wage type")
        if "schedule_pay" in employee_meta and not employee.get("schedule_pay"):
            missing.append("pay schedule")
        if "structure_id" in employee_meta and not employee.get("structure_id"):
            missing.append("payroll structure")
        if "structure_type_id" in employee_meta and not employee.get("structure_type_id"):
            missing.append("payroll structure type")
        if "work_entry_source" in employee_meta and not employee.get("work_entry_source"):
            missing.append("work-entry source")
        if "l10n_us_filing_status" in employee_meta and not employee.get("l10n_us_filing_status"):
            missing.append("federal filing status")
        if "l10n_us_state_filing_status" in employee_meta and not employee.get("l10n_us_state_filing_status"):
            missing.append("state filing status")
        if contract_meta and not open_contracts:
            missing.append("open contract")
        if not contract_meta and not any(field in employee_meta for field in ["wage", "structure_id", "schedule_pay"]):
            missing.append("contract/payroll fields unavailable")
        if not is_person:
            review.append("not enough person indicators")

        employee_rows.append(
            {
                "Employee ID": employee_id,
                "Employee": employee.get("name"),
                "Company": rel_name(employee.get("company_id")),
                "Department": rel_name(employee.get("department_id")),
                "Job": rel_name(employee.get("job_id")),
                "Working Schedule": rel_name(employee.get("resource_calendar_id")),
                "Employee Type": employee.get("employee_type", ""),
                "Has Email": yes_no(employee.get("work_email") or employee.get("private_email")),
                "Has Private Address": yes_no(employee.get("address_home_id")),
                "Has Bank Account": yes_no(bool(bank)),
                "Wage Set": yes_no(employee.get("wage") or employee.get("hourly_wage")),
                "Wage Type": employee.get("wage_type", ""),
                "Pay Schedule": employee.get("schedule_pay", ""),
                "Payroll Structure": rel_name(employee.get("structure_id")),
                "Payroll Structure Type": rel_name(employee.get("structure_type_id")),
                "Work Entry Source": employee.get("work_entry_source", ""),
                "Federal Filing Status": employee.get("l10n_us_filing_status", ""),
                "State Filing Status": employee.get("l10n_us_state_filing_status", ""),
                "Open/Relevant Contracts": len(open_contracts),
                "Ready for Test Payslip": yes_no(not missing),
                "Missing/Review": "; ".join(missing + review),
            }
        )

    with EMPLOYEE_REPORT.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = [
            "Employee ID",
            "Employee",
            "Company",
            "Department",
            "Job",
            "Working Schedule",
            "Employee Type",
            "Has Email",
            "Has Private Address",
            "Has Bank Account",
            "Wage Set",
            "Wage Type",
            "Pay Schedule",
            "Payroll Structure",
            "Payroll Structure Type",
            "Work Entry Source",
            "Federal Filing Status",
            "State Filing Status",
            "Open/Relevant Contracts",
            "Ready for Test Payslip",
            "Missing/Review",
        ]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(employee_rows)

    module_names = ["hr_payroll", "hr_payroll_account", "l10n_us_hr_payroll", "l10n_us_hr_payroll_account"]
    modules = search_read(
        models,
        db,
        uid,
        api_key,
        "ir.module.module",
        [("name", "in", module_names)],
        ["name", "state", "latest_version"],
        order="name asc",
    )
    module_lines = [f"- {module['name']}: {module['state']} {module.get('latest_version') or ''}".rstrip() for module in modules]

    ready = [row for row in employee_rows if row["Ready for Test Payslip"] == "yes"]
    not_ready = [row for row in employee_rows if row["Ready for Test Payslip"] == "no"]
    SUMMARY.write_text(
        f"""# Payroll Readiness Summary

Generated from live Odoo.

## Modules

{chr(10).join(module_lines)}

## Record Counts

- Employees: {len(employee_rows)}
- Payslips: {count(models, db, uid, api_key, "hr.payslip")}
- Pay runs: {count(models, db, uid, api_key, "hr.payslip.run")}
- Work-entry conflicts: {count(models, db, uid, api_key, "hr.work.entry", [("state", "=", "conflict")])}
- Employees ready for a test payslip by this audit: {len(ready)}
- Employees needing setup/review: {len(not_ready)}

## Main Blockers

{chr(10).join(f"- {row['Employee']}: {row['Missing/Review']}" for row in not_ready) if not_ready else "- No blockers found by this audit."}

## Reports

- Employee readiness: `{EMPLOYEE_REPORT.relative_to(ROOT).as_posix()}`
- Model/field audit: `{MODEL_REPORT.relative_to(ROOT).as_posix()}`
""",
        encoding="utf-8",
    )

    print(f"Connected uid: {uid}")
    print(f"Employees: {len(employee_rows)}")
    print(f"Employees ready for test payslip: {len(ready)}")
    print(f"Employees needing setup/review: {len(not_ready)}")
    print(f"Summary: {SUMMARY}")
    print(f"Employee report: {EMPLOYEE_REPORT}")
    print(f"Model report: {MODEL_REPORT}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
