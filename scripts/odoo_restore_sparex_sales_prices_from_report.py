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


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


def load_env() -> None:
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


def execute(models, db: str, uid: int, api_key: str, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def connect():
    load_env()
    socket.setdefaulttimeout(90)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return models, db, uid, api_key


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore Sparex list_price values from a specific apply report.")
    parser.add_argument("report_csv", type=Path)
    parser.add_argument("--apply", action="store_true", help="Actually restore prices. Default is dry run.")
    args = parser.parse_args()

    rows = list(csv.DictReader(args.report_csv.open(newline="", encoding="utf-8-sig")))
    candidates = {
        int(row["Product ID"]): row
        for row in rows
        if row.get("Product ID", "").isdigit()
        and row.get("Status") in {"Updated", "Pending Update"}
        and (row.get("Internal Reference", "").strip().upper()).startswith("S.")
        and money(row.get("Old Sales Price")) > 0
        and money(row.get("New Sales Price")) > 0
        and abs(money(row.get("Old Sales Price")) - money(row.get("New Sales Price"))) >= 0.005
    }

    models, db, uid, api_key = connect()
    products: list[dict[str, Any]] = []
    ids = sorted(candidates)
    for id_chunk in chunks(ids, 500):
        products.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [id_chunk],
                {"fields": ["id", "default_code", "name", "list_price"], "context": {"active_test": False}},
            )
        )

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = REPORT_DIR / f"odoo_sparex_sales_price_restore_report_{timestamp}.csv"
    fields = [
        "Timestamp",
        "Mode",
        "Status",
        "Product ID",
        "Internal Reference",
        "Name",
        "Current Sales Price",
        "Interrupted New Sales Price",
        "Restored Sales Price",
        "Notes",
    ]

    restore_jobs: list[tuple[int, float]] = []
    with output_path.open("w", newline="", encoding="utf-8-sig") as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for product in products:
            row = candidates[int(product["id"])]
            current = money(product.get("list_price"))
            interrupted_new = money(row.get("New Sales Price"))
            old_price = money(row.get("Old Sales Price"))
            if abs(current - interrupted_new) < 0.005:
                status = "Pending Restore" if not args.apply else "Restored"
                notes = "Current price matched interrupted apply value; restored from apply report old price."
                restore_jobs.append((int(product["id"]), old_price))
            else:
                status = "Skipped"
                notes = "Current price no longer matched interrupted apply value; left untouched."
            writer.writerow(
                {
                    "Timestamp": datetime.now().isoformat(timespec="seconds"),
                    "Mode": "Apply" if args.apply else "Dry Run",
                    "Status": status,
                    "Product ID": product.get("id"),
                    "Internal Reference": product.get("default_code", ""),
                    "Name": product.get("name", ""),
                    "Current Sales Price": f"{current:.2f}",
                    "Interrupted New Sales Price": f"{interrupted_new:.2f}",
                    "Restored Sales Price": f"{old_price:.2f}",
                    "Notes": notes,
                }
            )

    if args.apply:
        for product_id, old_price in restore_jobs:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"list_price": old_price}])

    print(f"Input report: {args.report_csv}")
    print(f"Restore report: {output_path}")
    print(f"Mode: {'Apply' if args.apply else 'Dry Run'}")
    print(f"Candidate changed rows: {len(candidates)}")
    print(f"Rows eligible for restore: {len(restore_jobs)}")
    if args.apply:
        print(f"Restored sales prices: {len(restore_jobs)}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
