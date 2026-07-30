from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"


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


def has_binary(value: Any) -> bool:
    if value in (False, None, ""):
        return False
    if isinstance(value, str):
        return value not in {"0", "False", "false"}
    return bool(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Unpublish public, non-service products that do not have a product photo.")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["readonly", "type"]})
    publish_fields = [field for field in ("is_published", "website_published") if field in fields_get and not fields_get[field].get("readonly")]
    if not publish_fields:
        raise SystemExit("No writable publish field found")
    type_field = "detailed_type" if "detailed_type" in fields_get else "type"

    domain: list[Any] = [
        ("active", "=", True),
        ("sale_ok", "=", True),
        (type_field, "!=", "service"),
        "|",
        ("is_published", "=", True),
        ("website_published", "=", True),
    ]
    product_ids = execute(models, db, uid, api_key, "product.template", "search", [domain], {"context": {"active_test": False}, "order": "id asc"})

    rows: list[dict[str, Any]] = []
    unpublish_ids: list[int] = []
    read_fields = ["id", "default_code", "name", "list_price", "public_categ_ids", "image_1920", "is_published", "website_published", type_field]
    read_fields = [field for field in read_fields if field in fields_get]
    for id_chunk in chunks(product_ids):
        products = execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": read_fields, "context": {"active_test": False, "bin_size": True}})
        for product in products:
            if has_binary(product.get("image_1920")):
                continue
            unpublish_ids.append(product["id"])
            rows.append({
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Sales Price": product.get("list_price") or 0,
                "Public Category Count": len(product.get("public_categ_ids") or []),
                "Action": "Unpublished" if args.apply else "Would unpublish",
                "Reason": "Published non-service product has no image_1920",
            })

    if args.apply and unpublish_ids:
        values = {field: False for field in publish_fields}
        for id_chunk in chunks(unpublish_ids):
            execute(models, db, uid, api_key, "product.template", "write", [id_chunk, values])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"published_missing_image_cleanup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Internal Reference", "Name", "Sales Price", "Public Category Count", "Action", "Reason"])
        writer.writeheader()
        writer.writerows(rows)

    print({
        "mode": "apply" if args.apply else "dry_run",
        "published_non_service_checked": len(product_ids),
        "missing_image_matches": len(unpublish_ids),
        "unpublished": len(unpublish_ids) if args.apply else 0,
        "report": str(report_path),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
