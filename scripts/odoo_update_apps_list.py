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

    result = models.execute_kw(db, uid, api_key, "ir.module.module", "update_list", [])
    print(f"Connected uid: {uid}")
    print(f"Apps list update result: {result}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.ProtocolError as exc:
        print(f"Odoo XML-RPC protocol error: {exc}", file=sys.stderr)
        raise SystemExit(1)
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
