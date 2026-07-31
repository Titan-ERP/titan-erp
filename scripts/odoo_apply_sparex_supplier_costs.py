from __future__ import annotations

import argparse
import csv
import os
import socket
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any

from odoo_runtime import ApplyGate, connect_legacy
from odoo_runtime.safety import append_audit

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


def load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def money(value: Any) -> float:
    if value in (None, False, ""):
        return 0.0
    try:
        return round(float(str(value).replace("$", "").replace(",", "").strip()), 2)
    except ValueError:
        return 0.0


def execute(
    models,
    db: str,
    uid: int,
    api_key: str,
    model: str,
    method: str,
    args: list[Any],
    kwargs: dict[str, Any] | None = None,
):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def connect():
    db, uid, api_key, models = connect_legacy(ENV_PATH)
    return models, db, uid, api_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply Sparex product standard cost from existing positive supplier/vendor prices.")
    parser.add_argument("--apply", action="store_true", help="Write product.template standard_price. Default is dry run.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--limit", type=int, default=0, help="Limit cost updates for this run.")
    args = parser.parse_args()

    models, db, uid, api_key = connect()
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [[("default_code", "=like", "S.%")]],
        {"context": {"active_test": False}},
    )

    products: list[dict[str, Any]] = []
    for id_chunk in chunks(product_ids, 500):
        products.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [id_chunk],
                {
                    "fields": ["id", "default_code", "name", "standard_price", "active", "sale_ok"],
                    "context": {"active_test": False},
                },
            )
        )

    supplier_rows: list[dict[str, Any]] = []
    for id_chunk in chunks(product_ids, 500):
        supplier_rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.supplierinfo",
                "search_read",
                [[("product_tmpl_id", "in", id_chunk), ("price", ">", 0)]],
                {"fields": ["product_tmpl_id", "product_code", "price", "partner_id"]},
            )
        )

    supplier_by_product: dict[int, dict[str, Any]] = {}
    for row in supplier_rows:
        product = row.get("product_tmpl_id")
        if not product:
            continue
        product_id = int(product[0])
        price = money(row.get("price"))
        if price <= 0:
            continue
        existing = supplier_by_product.get(product_id)
        if existing is None or price < money(existing.get("price")):
            supplier_by_product[product_id] = row

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    backup_path = REPORT_DIR / f"odoo_sparex_cost_backup_before_apply_{timestamp}.csv"
    report_path = REPORT_DIR / f"odoo_sparex_supplier_cost_apply_report_{timestamp}.csv"

    report_fields = [
        "Timestamp",
        "Mode",
        "Status",
        "Product ID",
        "Internal Reference",
        "Name",
        "Old Cost",
        "New Cost",
        "Supplier",
        "Supplier Product Code",
        "Active",
        "Sale OK",
        "Notes",
    ]
    update_jobs: list[tuple[int, float]] = []

    with backup_path.open("w", newline="", encoding="utf-8-sig") as backup_file, report_path.open("w", newline="", encoding="utf-8-sig") as report_file:
        backup_writer = csv.DictWriter(backup_file, fieldnames=["Product ID", "Internal Reference", "Name", "Old Cost", "Active", "Sale OK"])
        report_writer = csv.DictWriter(report_file, fieldnames=report_fields)
        backup_writer.writeheader()
        report_writer.writeheader()

        for product in products:
            supplier = supplier_by_product.get(int(product["id"]))
            old_cost = money(product.get("standard_price"))
            new_cost = money(supplier.get("price")) if supplier else 0.0
            backup_writer.writerow(
                {
                    "Product ID": product.get("id"),
                    "Internal Reference": product.get("default_code", ""),
                    "Name": product.get("name", ""),
                    "Old Cost": f"{old_cost:.2f}",
                    "Active": product.get("active"),
                    "Sale OK": product.get("sale_ok"),
                }
            )
            if not supplier:
                status = "No Positive Supplier Cost"
                notes = "Skipped; no existing positive product.supplierinfo price."
            elif abs(old_cost - new_cost) < 0.005:
                status = "Unchanged"
                notes = "Existing standard cost already matches lowest positive supplier price."
            else:
                status = "Pending Update" if not args.apply else "Updated"
                notes = "Standard cost sourced from lowest positive existing supplier/vendor price."
                update_jobs.append((int(product["id"]), new_cost))
            supplier_name = ""
            partner = supplier.get("partner_id") if supplier else None
            if isinstance(partner, list) and len(partner) > 1:
                supplier_name = str(partner[1])
            report_writer.writerow(
                {
                    "Timestamp": datetime.now().isoformat(timespec="seconds"),
                    "Mode": "Apply" if args.apply else "Dry Run",
                    "Status": status,
                    "Product ID": product.get("id"),
                    "Internal Reference": product.get("default_code", ""),
                    "Name": product.get("name", ""),
                    "Old Cost": f"{old_cost:.2f}",
                    "New Cost": f"{new_cost:.2f}" if new_cost else "",
                    "Supplier": supplier_name,
                    "Supplier Product Code": supplier.get("product_code", "") if supplier else "",
                    "Active": product.get("active"),
                    "Sale OK": product.get("sale_ok"),
                    "Notes": notes,
                }
            )

    if args.limit:
        update_jobs = update_jobs[: args.limit]
    if args.apply:
        gate = ApplyGate("sparex-supplier-costs", True, args.confirm, args.reason, args.max_records)
        gate.authorize(len(update_jobs))
        append_audit(
            ROOT / "outputs" / "write_audit" / "odoo_writes.jsonl",
            gate.audit_row(update_jobs, len(update_jobs)),
        )
        for product_id, new_cost in update_jobs:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"standard_price": new_cost}])

    print(f"Backup: {backup_path}")
    print(f"Report: {report_path}")
    print(f"Mode: {'Apply' if args.apply else 'Dry Run'}")
    print(f"Sparex products checked: {len(products)}")
    print(f"Products with positive supplier cost: {len(supplier_by_product)}")
    print(f"Rows needing cost update: {len(update_jobs)}")
    if args.apply:
        print(f"Applied cost updates: {len(update_jobs)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
