import csv
import os
import sys
import xmlrpc.client
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
TEMPLATE = ROOT / "odoo_imports" / "accounting" / "payroll_employee_setup_template.csv"
RESULTS = ROOT / "odoo_imports" / "accounting" / "payroll_employee_setup_apply_results.csv"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def fields_get(models, db, uid, api_key, model):
    return execute(models, db, uid, api_key, model, "fields_get", [], {"attributes": ["string", "type", "selection"]})


def selection_keys(fields_meta, field):
    values = fields_meta.get(field, {}).get("selection") or []
    return {key for key, _label in values}


def parse_money(value):
    text = (value or "").replace(",", "").replace("$", "").strip()
    if not text:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"invalid amount {value!r}")
    if amount <= 0:
        raise ValueError("amount must be greater than zero")
    return float(amount)


def normalize_choice(value):
    return (value or "").strip().lower()


def find_structure(models, db, uid, api_key, name):
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "hr.payroll.structure",
        "search_read",
        [[("name", "=", name)]],
        {"fields": ["id", "name"], "limit": 2},
    )
    if len(rows) != 1:
        raise ValueError(f"expected one payroll structure named {name!r}; found {len(rows)}")
    return rows[0]["id"]


def main():
    apply = "--apply" in sys.argv
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]

    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    employee_fields = fields_get(models, db, uid, api_key, "hr.employee")

    wage_type_keys = selection_keys(employee_fields, "wage_type")
    schedule_pay_keys = selection_keys(employee_fields, "schedule_pay")
    federal_status_keys = selection_keys(employee_fields, "l10n_us_filing_status")
    state_status_keys = selection_keys(employee_fields, "l10n_us_state_filing_status")

    rows = []
    writes = []
    with TEMPLATE.open("r", newline="", encoding="utf-8-sig") as f:
        for source in csv.DictReader(f):
            employee_id = int(source["Employee ID"])
            include = normalize_choice(source.get("Include in Payroll? (yes/no)"))
            if include != "yes":
                rows.append({"Employee ID": employee_id, "Employee": source.get("Employee"), "Status": "Skipped", "Message": "Include in Payroll is not yes"})
                continue

            messages = []
            values = {}
            try:
                wage = parse_money(source.get("Gross Wage or Hourly Rate"))
                values["wage"] = wage
            except ValueError as exc:
                messages.append(str(exc))

            for csv_field, odoo_field, allowed in [
                ("Pay Type (salary/hourly)", "wage_type", wage_type_keys),
                ("Pay Schedule", "schedule_pay", schedule_pay_keys),
                ("Federal Filing Status", "l10n_us_filing_status", federal_status_keys),
                ("State Filing Status", "l10n_us_state_filing_status", state_status_keys),
            ]:
                value = normalize_choice(source.get(csv_field))
                if not value:
                    messages.append(f"missing {csv_field}")
                elif allowed and value not in allowed:
                    messages.append(f"{csv_field} must be one of: {', '.join(sorted(allowed))}")
                elif odoo_field in employee_fields:
                    values[odoo_field] = value

            structure_name = (source.get("Payroll Structure") or "United States: Regular Pay").strip()
            if "structure_id" in employee_fields:
                try:
                    values["structure_id"] = find_structure(models, db, uid, api_key, structure_name)
                except ValueError as exc:
                    messages.append(str(exc))

            ssn = (source.get("SSN") or "").strip()
            if ssn:
                if "ssnid" in employee_fields:
                    values["ssnid"] = ssn
                if "identification_id" in employee_fields:
                    values["identification_id"] = ssn
            else:
                messages.append("missing SSN")

            if messages:
                rows.append({"Employee ID": employee_id, "Employee": source.get("Employee"), "Status": "Needs Data", "Message": "; ".join(messages)})
                continue
            writes.append((employee_id, values))
            rows.append({"Employee ID": employee_id, "Employee": source.get("Employee"), "Status": "Ready" if not apply else "Applied", "Message": f"{len(values)} employee fields"})

    if apply:
        for employee_id, values in writes:
            execute(models, db, uid, api_key, "hr.employee", "write", [[employee_id], values])

    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["Employee ID", "Employee", "Status", "Message"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Connected uid: {uid}")
    print(f"Mode: {'apply' if apply else 'dry_run'}")
    print(f"Rows ready/applied: {len(writes)}")
    print(f"Results: {RESULTS}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
