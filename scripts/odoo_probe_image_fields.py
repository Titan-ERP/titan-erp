import os
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
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    fields = models.execute_kw(db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["string", "type", "readonly"]})
    for name, meta in sorted(fields.items()):
        label = meta.get("string") or ""
        if "image" in name.lower() or "image" in label.lower():
            print(f"{name}|{label}|{meta.get('type')}|readonly={meta.get('readonly')}")


if __name__ == "__main__":
    main()
