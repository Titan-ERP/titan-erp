import argparse
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
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--status",
        required=True,
        choices=[
            "ms_single",
            "ms_head_of_family",
            "ms_married_spouse_not_employed",
            "ms_married_both_spouses_employed",
        ],
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]

    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    fields = execute(
        models,
        db,
        uid,
        api_key,
        "hr.employee",
        "fields_get",
        [],
        {"attributes": ["selection"]},
    )
    state_statuses = dict(fields["l10n_us_state_filing_status"].get("selection") or [])
    if args.status not in state_statuses:
        raise SystemExit(
            f"{args.status!r} is not available in Odoo yet. Deploy/install l10n_us_hr_payroll_ms_status first."
        )

    employees = execute(
        models,
        db,
        uid,
        api_key,
        "hr.employee",
        "search_read",
        [[("name", "=", EMPLOYEE_NAME)]],
        {"fields": ["id", "name", "l10n_us_state_filing_status"], "limit": 2},
    )
    if len(employees) != 1:
        raise SystemExit(f"Expected exactly one employee named {EMPLOYEE_NAME!r}; found {len(employees)}")

    employee = employees[0]
    print(f"Connected uid: {uid}")
    print(f"Employee: {employee['id']} {employee['name']}")
    print(f"Current state filing status: {employee.get('l10n_us_state_filing_status') or 'blank'}")
    print(f"Requested state filing status: {args.status} ({state_statuses[args.status]})")

    if args.apply:
        execute(
            models,
            db,
            uid,
            api_key,
            "hr.employee",
            "write",
            [[employee["id"]], {"l10n_us_state_filing_status": args.status}],
        )
        print("Applied state tax filing status.")
    else:
        print("Dry run only. Re-run with --apply after confirming the signed Mississippi certificate.")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.ProtocolError as exc:
        print(f"Odoo XML-RPC protocol error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
