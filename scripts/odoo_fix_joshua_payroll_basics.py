import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
EMPLOYEE_NAME = "Joshua McLain"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


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

    rows = execute(
        models,
        db,
        uid,
        api_key,
        "hr.employee",
        "search_read",
        [[("name", "=", EMPLOYEE_NAME)]],
        {"fields": ["id", "name", "identification_id", "ssnid", "wage", "hourly_wage", "wage_type", "l10n_us_state_filing_status"], "limit": 2},
    )
    if len(rows) != 1:
        raise SystemExit(f"Expected exactly one employee named {EMPLOYEE_NAME!r}; found {len(rows)}")

    employee = rows[0]
    values = {}
    if not employee.get("identification_id") and employee.get("ssnid"):
        values["identification_id"] = employee["ssnid"]

    print(f"Connected uid: {uid}")
    print(f"Employee: {employee['id']} {employee['name']}")
    print(f"Employee ID currently set: {'yes' if employee.get('identification_id') else 'no'}")
    print(f"SSN source present: {'yes' if employee.get('ssnid') else 'no'}")
    print(f"Pay type/rate: {employee.get('wage_type') or 'unknown'} / hourly {employee.get('hourly_wage')}")
    print(f"State filing status set: {'yes' if employee.get('l10n_us_state_filing_status') else 'no'}")

    if not values:
        print("No safe basic updates needed.")
        return

    if apply:
        execute(models, db, uid, api_key, "hr.employee", "write", [[employee["id"]], values])
        print(f"Applied {len(values)} safe basic field update(s).")
    else:
        print(f"Dry run: would apply {len(values)} safe basic field update(s). Re-run with --apply to write.")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.ProtocolError as exc:
        print(f"Odoo XML-RPC protocol error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
