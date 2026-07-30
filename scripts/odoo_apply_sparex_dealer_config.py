from __future__ import annotations

import argparse
import os
import xmlrpc.client
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync local Sparex dealer env values into Odoo system parameters.")
    parser.add_argument("--apply", action="store_true", help="Write ir.config_parameter values. Default is dry run.")
    args = parser.parse_args()

    load_env()
    values = {
        "southern_parts_intelligence.sparex_dealer_login_url": required("SPAREX_DEALER_LOGIN_URL"),
        "southern_parts_intelligence.sparex_dealer_products_url": os.environ.get("SPAREX_DEALER_PRODUCTS_URL", "https://us.sparex.com/").strip()
        or "https://us.sparex.com/",
        "southern_parts_intelligence.sparex_dealer_username": required("SPAREX_DEALER_USERNAME"),
        "southern_parts_intelligence.sparex_dealer_password": required("SPAREX_DEALER_PASSWORD"),
    }

    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    if args.apply:
        for key, value in values.items():
            execute(models, db, uid, api_key, "ir.config_parameter", "set_param", [key, value])

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "configured_keys": [
                "southern_parts_intelligence.sparex_dealer_login_url",
                "southern_parts_intelligence.sparex_dealer_products_url",
                "southern_parts_intelligence.sparex_dealer_username",
                "southern_parts_intelligence.sparex_dealer_password",
            ],
            "password": "<redacted>",
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
