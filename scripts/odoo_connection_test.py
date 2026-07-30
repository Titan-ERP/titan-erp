import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env(path):
    if not path.exists():
        raise SystemExit(f"Missing {path}. Copy odoo_connection.env.example and fill it in.")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def main():
    load_env(ENV_PATH)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")

    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed. Check ODOO_URL, ODOO_DB, ODOO_USERNAME, and ODOO_API_KEY.")

    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    product_count = models.execute_kw(db, uid, api_key, "product.template", "search_count", [[]])
    category_count = models.execute_kw(db, uid, api_key, "product.category", "search_count", [[]])
    partner_count = models.execute_kw(db, uid, api_key, "res.partner", "search_count", [[]])

    print(f"Connected to Odoo as uid={uid}")
    print(f"Products: {product_count}")
    print(f"Product categories: {category_count}")
    print(f"Contacts/vendors: {partner_count}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
