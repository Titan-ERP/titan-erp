import os
import sys
import uuid
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

    key = f"codex.state_status_method.{uuid.uuid4()}"
    model_rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.model",
        "search_read",
        [[("model", "=", "hr.version")]],
        {"fields": ["id"], "limit": 1},
    )
    if not model_rows:
        raise SystemExit("hr.version model not found.")

    code = f"""
selection = env['hr.version']._get_selection_state_filing_status()
env['ir.config_parameter'].sudo().set_param({key!r}, repr(selection))
"""
    action_id = execute(
        models,
        db,
        uid,
        api_key,
        "ir.actions.server",
        "create",
        [
            {
                "name": "Codex temporary state status method call",
                "model_id": model_rows[0]["id"],
                "state": "code",
                "code": code,
            }
        ],
    )
    try:
        execute(models, db, uid, api_key, "ir.actions.server", "run", [[action_id]])
        value = execute(models, db, uid, api_key, "ir.config_parameter", "get_param", [key])
        print(f"Connected uid: {uid}")
        print(value)
    finally:
        execute(models, db, uid, api_key, "ir.actions.server", "unlink", [[action_id]])
        param_ids = execute(models, db, uid, api_key, "ir.config_parameter", "search", [[("key", "=", key)]])
        if param_ids:
            execute(models, db, uid, api_key, "ir.config_parameter", "unlink", [param_ids])


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.ProtocolError as exc:
        print(f"Odoo XML-RPC protocol error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
