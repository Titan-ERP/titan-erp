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

    for model in ["hr.employee", "hr.payroll.structure", "hr.salary.rule", "hr.work.entry.type"]:
        fields = execute(
            models,
            db,
            uid,
            api_key,
            model,
            "fields_get",
            [],
            {"attributes": ["string", "type", "relation", "selection", "required"]},
        )
        print(f"\nMODEL {model}")
        for name, values in sorted(fields.items()):
            text = f"{name} {values.get('string')} {values.get('relation')}".lower()
            if any(term in text for term in ["tax status", "state", "filing", "mississippi", "l10n_us", "withholding"]):
                print(f"- {name}: {values}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
