import os
import sys
import time
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


def module_record(models, db, uid, api_key, module_name):
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.module.module",
        "search_read",
        [[("name", "=", module_name)]],
        {"fields": ["name", "shortdesc", "state", "latest_version"], "limit": 2},
    )
    if len(rows) != 1:
        raise SystemExit(f"Expected one module named {module_name!r}; found {len(rows)}")
    return rows[0]


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: odoo_upgrade_module.py MODULE_NAME")
    module_name = sys.argv[1]

    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]

    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    module = module_record(models, db, uid, api_key, module_name)
    print(f"Connected uid: {uid}")
    print(f"Before: {module['name']} [{module['state']}] {module.get('latest_version') or ''}")
    if module["state"] != "installed":
        raise SystemExit(f"Module is {module['state']!r}; upgrade requires installed state.")

    execute(models, db, uid, api_key, "ir.module.module", "button_immediate_upgrade", [[module["id"]]])

    for _ in range(20):
        time.sleep(3)
        module = module_record(models, db, uid, api_key, module_name)
        if module["state"] == "installed":
            break
    print(f"After: {module['name']} [{module['state']}] {module.get('latest_version') or ''}")
    if module["state"] != "installed":
        raise SystemExit(f"Upgrade did not reach installed state; current state: {module['state']}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.ProtocolError as exc:
        print(f"Odoo XML-RPC protocol error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
