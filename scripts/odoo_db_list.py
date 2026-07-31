import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    load_env(ENV_PATH)
    url = os.environ["ODOO_URL"].rstrip("/")
    db = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/db")
    print(db.list())


if __name__ == "__main__":
    main()
