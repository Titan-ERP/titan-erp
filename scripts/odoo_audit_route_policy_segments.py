from __future__ import annotations

import os
import socket
from collections import Counter
from pathlib import Path
from typing import Any
import xmlrpc.client

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[int], size: int = 500):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def segment(product: dict[str, Any]) -> str:
    code = (product.get("default_code") or "").upper().strip()
    qty = float(product.get("qty_available") or 0)
    if qty > 0:
        return "stocked_on_hand"
    if code.startswith("S."):
        return "sparex_catalog_zero_stock"
    if code.startswith("BLQ"):
        return "blumaq_catalog_zero_stock"
    if code.startswith("PAR-"):
        return "southern_par_zero_stock"
    return "other_zero_stock"


def main() -> None:
    socket.setdefaulttimeout(90)
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    type_field = "detailed_type" if "detailed_type" in fields_get else "type"
    ids = execute(models, db, uid, api_key, "product.template", "search", [[("active", "=", True), (type_field, "!=", "service")]], {"context": {"active_test": False}})
    counts: Counter[str] = Counter()
    for id_chunk in chunks(ids):
        products = execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": ["id", "default_code", "name", "qty_available", "route_ids", type_field], "context": {"active_test": False}})
        for product in products:
            seg = segment(product)
            counts[seg] += 1
            if product.get("route_ids"):
                counts[f"{seg}_has_routes"] += 1
    print(dict(counts))


if __name__ == "__main__":
    main()
