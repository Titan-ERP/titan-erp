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


def rel(value):
    if isinstance(value, list) and len(value) > 1:
        return f"{value[0]}:{value[1]}"
    return value


def print_record(title, row):
    print(f"\n{title}")
    for key, value in row.items():
        print(f"- {key}: {rel(value)}")


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
    print(f"Connected uid: {uid}")

    employee_fields = execute(
        models,
        db,
        uid,
        api_key,
        "hr.employee",
        "fields_get",
        [],
        {"attributes": ["string", "type", "selection", "relation"]},
    )
    wanted_employee_fields = [
        "id",
        "name",
        "active",
        "company_id",
        "department_id",
        "job_id",
        "employee_type",
        "work_email",
        "work_phone",
        "resource_calendar_id",
        "address_home_id",
        "bank_account_id",
        "primary_bank_account_id",
        "identification_id",
        "ssnid",
        "wage",
        "hourly_wage",
        "wage_type",
        "schedule_pay",
        "structure_id",
        "structure_type_id",
        "work_entry_source",
        "l10n_us_filing_status",
        "l10n_us_state_filing_status",
        "l10n_us_state_withholding_allowance",
        "l10n_us_state_extra_withholding",
    ]
    fields = [field for field in wanted_employee_fields if field in employee_fields]
    employees = execute(
        models,
        db,
        uid,
        api_key,
        "hr.employee",
        "search_read",
        [[("name", "ilike", "Joshua"), ("name", "ilike", "McLain")]],
        {"fields": fields, "limit": 10},
    )
    if not employees:
        raise SystemExit("No employee matching Joshua McLain found.")
    for employee in employees:
        print_record("Employee", employee)

        employee_id = employee["id"]
        if model_exists(models, db, uid, api_key, "hr.contract"):
            contract_fields = execute(
                models,
                db,
                uid,
                api_key,
                "hr.contract",
                "fields_get",
                [],
                {"attributes": ["string", "type", "relation"]},
            )
            wanted_contract_fields = [
                "id",
                "name",
                "employee_id",
                "state",
                "date_start",
                "date_end",
                "wage",
                "hourly_wage",
                "wage_type",
                "schedule_pay",
                "resource_calendar_id",
                "structure_type_id",
            ]
            contract_read_fields = [field for field in wanted_contract_fields if field in contract_fields]
            contracts = execute(
                models,
                db,
                uid,
                api_key,
                "hr.contract",
                "search_read",
                [[("employee_id", "=", employee_id)]],
                {"fields": contract_read_fields, "limit": 10, "order": "date_start desc, id desc"},
            )
            for contract in contracts:
                print_record("Contract", contract)
        else:
            print("\nContract\n- hr.contract model is not installed in this database")

    partner_fields = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "fields_get",
        [],
        {"attributes": ["string", "type", "relation"]},
    )
    partner_read_fields = [
        field
        for field in ["id", "name", "active", "email", "phone", "mobile", "street", "city", "state_id", "zip"]
        if field in partner_fields
    ]
    partners = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "search_read",
        [[("name", "ilike", "Joshua McLain")]],
        {"fields": partner_read_fields, "limit": 20},
    )
    for partner in partners:
        print_record("Matching Partner", partner)


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.ProtocolError as exc:
        print(f"Odoo XML-RPC protocol error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
