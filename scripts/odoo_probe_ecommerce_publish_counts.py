from __future__ import annotations

import os
from pathlib import Path
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
for line in (ROOT / "odoo_connection.env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))

url = os.environ["ODOO_URL"].rstrip("/")
db = os.environ["ODOO_DB"]
username = os.environ["ODOO_USERNAME"]
api_key = os.environ["ODOO_API_KEY"]
uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

fields = models.execute_kw(db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
print({"has_is_published": "is_published" in fields, "has_website_published": "website_published" in fields})

base = [("active", "=", True), ("sale_ok", "=", True)]
checks = {
    "all_active_saleable": [],
    "is_published_true": [("is_published", "=", True)],
    "website_published_true": [("website_published", "=", True)],
    "priced_over_1": [("list_price", ">", 1.0)],
    "priced_is_published": [("list_price", ">", 1.0), ("is_published", "=", True)],
    "priced_website_published": [("list_price", ">", 1.0), ("website_published", "=", True)],
    "has_public_category": [("public_categ_ids", "!=", False)],
    "priced_has_public_category": [("list_price", ">", 1.0), ("public_categ_ids", "!=", False)],
}
for name, extra in checks.items():
    try:
        count = models.execute_kw(
            db,
            uid,
            api_key,
            "product.template",
            "search_count",
            [base + extra],
            {"context": {"active_test": False}},
        )
        print(f"{name}: {count}")
    except Exception as exc:
        print(f"{name}: ERROR {exc}")
