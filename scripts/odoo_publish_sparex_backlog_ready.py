from __future__ import annotations

import argparse
import csv
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "run_reports"


def load_env() -> None:
    for raw_line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    socket.setdefaulttimeout(90)
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
        except (OSError, TimeoutError, xmlrpc.client.ProtocolError) as exc:
            last_exc = exc
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise last_exc or RuntimeError("Unknown Odoo XML-RPC failure")


def has_binary(value: Any) -> bool:
    if value in (False, None, ""):
        return False
    if isinstance(value, str):
        return value not in {"0", "False", "false"}
    return bool(value)


def clean(value: Any) -> str:
    if value in (False, None):
        return ""
    return str(value).strip()


def load_backlog_ids(path: Path, limit: int) -> list[int]:
    product_ids: list[int] = []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Priority Bucket") != "P2 Website Ready To Publish":
                continue
            product_id = clean(row.get("Product ID"))
            if not product_id.isdigit():
                continue
            product_ids.append(int(product_id))
            if limit and len(product_ids) >= limit:
                break
    return product_ids


def main() -> int:
    parser = argparse.ArgumentParser(description="Publish only backlog-verified website-ready Sparex products.")
    parser.add_argument("backlog_csv", type=Path)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    product_ids = load_backlog_ids(args.backlog_csv, args.limit)
    db, uid, api_key, models = connect()
    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["readonly"]})
    description_fields = [field for field in ("description_ecommerce", "website_description", "description_sale") if field in fields_get]
    publish_values: dict[str, Any] = {}
    for field in ("website_published", "is_published"):
        if field in fields_get and not fields_get[field].get("readonly"):
            publish_values[field] = True
    if not publish_values:
        raise SystemExit("No writable website publish field found.")

    wanted = [
        "id",
        "default_code",
        "name",
        "active",
        "sale_ok",
        "standard_price",
        "list_price",
        "public_categ_ids",
        "image_1920",
        "seller_ids",
        "southern_source_url",
        "is_published",
        "website_published",
        "website_url",
        *description_fields,
    ]
    wanted = [field for field in wanted if field in fields_get]
    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [product_ids],
        {"fields": wanted, "context": {"active_test": False, "bin_size": True}},
    )

    rows: list[dict[str, Any]] = []
    publish_ids: list[int] = []
    for product in products:
        sku = clean(product.get("default_code"))
        cost = float(product.get("standard_price") or 0.0)
        price = float(product.get("list_price") or 0.0)
        descriptions = [clean(product.get(field)) for field in description_fields]
        internal_copy = any(
            marker.lower() in description.lower()
            for description in descriptions
            for marker in ("detail enrichment pending", "pricing requires separate review", "public blumaq page harvested")
        )
        ready = (
            sku.upper().startswith("S.")
            and bool(product.get("active"))
            and bool(product.get("sale_ok"))
            and cost > 0
            and price > 1
            and price > cost
            and bool(product.get("public_categ_ids"))
            and has_binary(product.get("image_1920"))
            and bool(product.get("seller_ids"))
            and clean(product.get("southern_source_url")).startswith(("http://", "https://"))
            and any(descriptions)
            and not internal_copy
        )
        already_published = bool(product.get("website_published")) or bool(product.get("is_published"))
        status = "Ready To Publish"
        if already_published:
            status = "Already Published"
        elif not ready:
            status = "Skipped Failed Live Gate"
        elif args.apply:
            publish_ids.append(int(product["id"]))
            status = "Published"
        rows.append(
            {
                "Product ID": product.get("id"),
                "Internal Reference": sku,
                "Name": clean(product.get("name")),
                "Cost": f"{cost:.2f}",
                "Sales Price": f"{price:.2f}",
                "Has Website Category": "yes" if product.get("public_categ_ids") else "no",
                "Has Image": "yes" if has_binary(product.get("image_1920")) else "no",
                "Has Supplierinfo": "yes" if product.get("seller_ids") else "no",
                "Source URL": clean(product.get("southern_source_url")),
                "Website URL": clean(product.get("website_url")),
                "Status": status,
            }
        )

    if publish_ids:
        execute(models, db, uid, api_key, "product.template", "write", [publish_ids, publish_values])

    if publish_ids:
        verified = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [publish_ids],
            {"fields": ["id", "website_published", "is_published", "website_url"], "context": {"active_test": False}},
        )
        verified_by_id = {int(row["id"]): row for row in verified}
        for row in rows:
            product_id = int(row["Product ID"] or 0)
            if product_id in verified_by_id:
                verified_row = verified_by_id[product_id]
                row["Website URL"] = clean(verified_row.get("website_url"))
                if not (verified_row.get("website_published") or verified_row.get("is_published")):
                    row["Status"] = "Publish Verification Failed"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"sparex_backlog_ready_publish_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    fieldnames = [
        "Product ID",
        "Internal Reference",
        "Name",
        "Cost",
        "Sales Price",
        "Has Website Category",
        "Has Image",
        "Has Supplierinfo",
        "Source URL",
        "Website URL",
        "Status",
    ]
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "input_candidates": len(product_ids),
            "published": len(publish_ids),
            "ready": sum(1 for row in rows if row["Status"] == "Ready To Publish"),
            "already_published": sum(1 for row in rows if row["Status"] == "Already Published"),
            "skipped": sum(1 for row in rows if row["Status"].startswith("Skipped")),
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
