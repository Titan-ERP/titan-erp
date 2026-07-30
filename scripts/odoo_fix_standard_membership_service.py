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


def ensure_category(models, db, uid, api_key, complete_name: str) -> int:
    parent_id = False
    path = []
    for part in [piece.strip() for piece in complete_name.split("/") if piece.strip()]:
        path.append(part)
        partial = " / ".join(path)
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "product.category",
            "search_read",
            [[("complete_name", "=", partial)]],
            {"fields": ["id"], "limit": 1},
        )
        if rows:
            parent_id = rows[0]["id"]
            continue
        values = {"name": part}
        if parent_id:
            values["parent_id"] = parent_id
        parent_id = execute(models, db, uid, api_key, "product.category", "create", [values])
    return int(parent_id)


def main() -> None:
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]

    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [[("name", "ilike", "Standard Membership")]],
        {"context": {"active_test": False}},
    )
    if not ids:
        raise SystemExit("No product found matching Standard Membership.")

    fields = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "fields_get",
        [["type", "is_storable", "sale_ok", "purchase_ok", "invoice_policy", "service_tracking"]],
        {"attributes": ["readonly"]},
    )
    values = {
        "type": "service",
        "sale_ok": True,
        "purchase_ok": False,
    }
    if "is_storable" in fields and not fields["is_storable"].get("readonly"):
        values["is_storable"] = False
    if "invoice_policy" in fields and not fields["invoice_policy"].get("readonly"):
        values["invoice_policy"] = "order"
    if "service_tracking" in fields and not fields["service_tracking"].get("readonly"):
        values["service_tracking"] = "no"
    values["categ_id"] = ensure_category(models, db, uid, api_key, "Services / Membership")

    execute(models, db, uid, api_key, "product.template", "write", [ids, values])
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [ids],
        {"fields": ["id", "name", "default_code", "type", "is_storable", "sale_ok", "purchase_ok"], "context": {"active_test": False}},
    )

    print(f"Updated {len(ids)} product(s) matching Standard Membership.")
    for row in rows:
        print(
            f"{row['id']}: {row.get('name')} | type={row.get('type')} | "
            f"is_storable={row.get('is_storable')} | sale_ok={row.get('sale_ok')} | purchase_ok={row.get('purchase_ok')}"
        )


if __name__ == "__main__":
    main()
