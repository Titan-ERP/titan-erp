from __future__ import annotations

import argparse
import csv
import os
import socket
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"
BUY_ROUTE_NAME = "Buy"
MTO_ROUTE_NAME = "Replenish on Order (MTO)"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[int], size: int = 300):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def rel_name(value: Any) -> str:
    return value[1] if isinstance(value, list | tuple) and len(value) > 1 else ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Set active Odoo parts/products to delivered quantities and a stock-first procurement route policy.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-services", action="store_true", help="Also update service products. Off by default to protect memberships/subscriptions/labor.")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument(
        "--mto-buy",
        action="store_true",
        help="Use explicit MTO + Buy on products. Default is safer stock-first: Buy route on, MTO route removed.",
    )
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

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

    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["selection", "readonly", "type"]})
    type_field = "detailed_type" if "detailed_type" in fields_get else "type"
    required_fields = ["invoice_policy", "route_ids", type_field]
    missing_fields = [field for field in required_fields if field not in fields_get]
    if missing_fields:
        raise SystemExit(f"Missing product.template fields: {missing_fields}")

    selection = dict(fields_get["invoice_policy"].get("selection") or [])
    if "delivery" not in selection:
        raise SystemExit(f"invoice_policy has no 'delivery' value. Selection={selection}")

    route_rows = execute(
        models,
        db,
        uid,
        api_key,
        "stock.route",
        "search_read",
        [[("name", "in", [BUY_ROUTE_NAME, MTO_ROUTE_NAME])]],
        {"fields": ["id", "name", "active"], "context": {"active_test": False}, "limit": 10},
    )
    routes_by_name = {row["name"]: row for row in route_rows}
    required_route_names = [BUY_ROUTE_NAME, MTO_ROUTE_NAME] if args.mto_buy else [BUY_ROUTE_NAME]
    missing_routes = [name for name in required_route_names if name not in routes_by_name]
    if missing_routes:
        raise SystemExit(f"Missing routes: {missing_routes}")
    buy_route_id = routes_by_name[BUY_ROUTE_NAME]["id"]
    mto_route_id = routes_by_name[MTO_ROUTE_NAME]["id"] if MTO_ROUTE_NAME in routes_by_name else None
    target_route_ids = [routes_by_name[name]["id"] for name in required_route_names]

    domain: list[Any] = []
    if not args.include_archived:
        domain.append(("active", "=", True))
    if not args.include_services:
        domain.append((type_field, "!=", "service"))
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"context": {"active_test": False}, "order": "id asc", "limit": args.limit or 0},
    )

    rows: list[dict[str, Any]] = []
    to_update_invoice: list[int] = []
    to_add_routes: list[int] = []
    to_remove_mto: list[int] = []
    read_fields = ["id", "default_code", "name", "active", type_field, "invoice_policy", "route_ids"]
    for id_chunk in chunks(product_ids, 500):
        products = execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": read_fields, "context": {"active_test": False}})
        for product in products:
            route_ids = set(product.get("route_ids") or [])
            missing_route_ids = [route_id for route_id in target_route_ids if route_id not in route_ids]
            has_mto = bool(mto_route_id and mto_route_id in route_ids)
            needs_invoice = product.get("invoice_policy") != "delivery"
            needs_add_routes = bool(missing_route_ids)
            needs_remove_mto = bool(has_mto and not args.mto_buy)
            if needs_invoice:
                to_update_invoice.append(product["id"])
            if needs_add_routes:
                to_add_routes.append(product["id"])
            if needs_remove_mto:
                to_remove_mto.append(product["id"])
            rows.append({
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Active": product.get("active"),
                "Product Type": product.get(type_field),
                "Current Invoice Policy": product.get("invoice_policy") or "",
                "Missing Routes": "; ".join(name for name, rid in zip(required_route_names, target_route_ids) if rid in missing_route_ids),
                "Action": "; ".join(
                    action
                    for action, applies in [
                        ("Set delivered quantities", needs_invoice),
                        ("Add Buy route" if not args.mto_buy else "Add MTO+Buy routes", needs_add_routes),
                        ("Remove MTO for stock-first behavior", needs_remove_mto),
                    ]
                    if applies
                )
                or "Already compliant",
            })

    if args.apply:
        for id_chunk in chunks(to_update_invoice):
            execute(models, db, uid, api_key, "product.template", "write", [id_chunk, {"invoice_policy": "delivery"}])
        route_commands = [(4, route_id) for route_id in target_route_ids]
        for id_chunk in chunks(to_add_routes):
            execute(models, db, uid, api_key, "product.template", "write", [id_chunk, {"route_ids": route_commands}])
        if to_remove_mto and mto_route_id:
            for id_chunk in chunks(to_remove_mto):
                execute(models, db, uid, api_key, "product.template", "write", [id_chunk, {"route_ids": [(3, mto_route_id)]}])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"product_procurement_policy_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Internal Reference", "Name", "Active", "Product Type", "Current Invoice Policy", "Missing Routes", "Action"])
        writer.writeheader()
        writer.writerows(rows)

    print({
        "mode": "apply" if args.apply else "dry_run",
        "scope": "all product types" if args.include_services else "non-service products",
        "include_archived": args.include_archived,
        "products_checked": len(product_ids),
        "invoice_policy_updates": len(to_update_invoice),
        "route_add_updates": len(to_add_routes),
        "mto_remove_updates": len(to_remove_mto),
        "target_routes": routes_by_name,
        "policy": "MTO + Buy" if args.mto_buy else "Stock-first: Buy route, no forced MTO",
        "report": str(report_path),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

