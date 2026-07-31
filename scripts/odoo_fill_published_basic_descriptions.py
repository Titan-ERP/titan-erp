from __future__ import annotations

import argparse
import csv
import html
import os
import re
import socket
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "outputs"

INTERNAL_MARKERS = (
    "detail enrichment pending",
    "pricing requires separate review",
    "public blumaq page harvested",
    "source url",
    "harvested",
)


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    socket.setdefaulttimeout(90)
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[int], size: int = 500):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def customer_ready(value: Any) -> bool:
    text = clean_text(value)
    if not text:
        return False
    lowered = text.lower()
    return not any(marker in lowered for marker in INTERNAL_MARKERS)


def is_visible_part_reference(code: str) -> bool:
    code = code.upper()
    return bool(code and not code.startswith(("SEC-", "TMP-", "PRO-", "PAR-")))


def source_label(code: str) -> str:
    upper = code.upper()
    if upper.startswith("S."):
        return "Sparex"
    if upper.startswith("BLQ-"):
        return "Blumaq"
    return "OEM"


def website_copy(name: str, code: str) -> str:
    label = source_label(code)
    ref_text = f"{label} reference {code}" if is_visible_part_reference(code) else f"reference {code}"
    return (
        '<div class="se-product-summary">'
        f"<p>{html.escape(name)} identified by {html.escape(ref_text)}.</p>"
        "<ul>"
        f"<li><strong>Reference:</strong> {html.escape(code)}</li>"
        "</ul>"
        "<p>Confirm dimensions, OEM reference, and machine fitment before ordering.</p>"
        "</div>"
    )


def sale_copy(name: str, code: str) -> str:
    label = source_label(code)
    ref_text = f"{label} reference {code}" if is_visible_part_reference(code) else f"reference {code}"
    return f"{name} identified by {ref_text}. Confirm dimensions, OEM reference, and machine fitment before ordering."


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill basic customer descriptions for published parts.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    published_field = "website_published" if "website_published" in fields else "is_published"
    type_field = "detailed_type" if "detailed_type" in fields else "type"
    description_fields = [field for field in ("description_ecommerce", "website_description", "description_sale") if field in fields]
    website_field = "website_description" if "website_description" in fields else description_fields[0]

    domain = [
        ("active", "=", True),
        ("sale_ok", "=", True),
        (published_field, "=", True),
        (type_field, "!=", "service"),
    ]
    ids = execute(models, db, uid, api_key, "product.template", "search", [domain], {"order": "id asc", "limit": args.limit or 0})

    rows: list[dict[str, Any]] = []
    updated = 0
    read_fields = ["id", "default_code", "name", "list_price"] + description_fields
    for id_chunk in chunks(ids):
        products = execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": read_fields})
        for product in products:
            code = clean_text(product.get("default_code"))
            name = clean_text(product.get("name"))
            has_ready = any(customer_ready(product.get(field)) for field in description_fields)
            if has_ready or not code or not name:
                continue
            values = {
                website_field: website_copy(name, code),
                "description_sale": sale_copy(name, code),
            }
            if "description_ecommerce" in description_fields:
                values["description_ecommerce"] = website_copy(name, code)
            if args.apply:
                execute(models, db, uid, api_key, "product.template", "write", [[product["id"]], values])
                updated += 1
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": code,
                    "Name": name,
                    "Sales Price": product.get("list_price") or 0,
                    "Status": "Updated" if args.apply else "Would update",
                }
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = OUT_DIR / f"published_basic_description_fill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Internal Reference", "Name", "Sales Price", "Status"])
        writer.writeheader()
        writer.writerows(rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "published_priced_parts_checked": len(ids),
            "matched": len(rows),
            "updated": updated,
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
