from __future__ import annotations

import os
import xmlrpc.client
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def main() -> None:
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    ids = execute(models, db, uid, api_key, "product.template", "search", [[("name", "ilike", "Standard Membership")]], {"context": {"active_test": False}})
    all_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["string", "type", "readonly"]})
    wanted = [
        "id", "name", "default_code", "active", "sale_ok", "purchase_ok", "type", "detailed_type", "is_storable",
        "list_price", "website_published", "is_published", "website_id", "website_url", "website_sequence",
        "allow_out_of_stock_order", "available_threshold", "show_availability", "out_of_stock_message",
        "product_variant_ids", "valid_product_template_attribute_line_ids", "attribute_line_ids", "combination_indices",
        "recurring_invoice", "subscription_template_id", "service_tracking", "invoice_policy", "uom_id", "uom_po_id",
        "optional_product_ids", "alternative_product_ids", "accessory_product_ids",
    ]
    fields = [field for field in wanted if field in all_fields]
    rows = execute(models, db, uid, api_key, "product.template", "read", [ids], {"fields": fields, "context": {"active_test": False}})
    print("TEMPLATE")
    for row in rows:
        for key in fields:
            print(f"{key}: {row.get(key)}")
        print("---")

    variant_ids = []
    for row in rows:
        variant_ids.extend(row.get("product_variant_ids") or [])
    if variant_ids:
        variant_all = execute(models, db, uid, api_key, "product.product", "fields_get", [], {"attributes": ["string", "type", "readonly"]})
        variant_wanted = ["id", "display_name", "active", "product_tmpl_id", "sale_ok", "lst_price", "website_published", "is_published", "combination_indices"]
        variant_fields = [field for field in variant_wanted if field in variant_all]
        variants = execute(models, db, uid, api_key, "product.product", "read", [variant_ids], {"fields": variant_fields, "context": {"active_test": False}})
        print("VARIANTS")
        for row in variants:
            for key in variant_fields:
                print(f"{key}: {row.get(key)}")
            print("---")


if __name__ == "__main__":
    main()
