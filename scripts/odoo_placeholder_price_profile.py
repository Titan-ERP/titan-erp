"""Read-only profile of live Odoo products still blocked by placeholder pricing."""

from __future__ import annotations

import csv
import os
import socket
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports/product_master/pricing"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ[key.strip()] = value.strip().strip('"').strip("'")


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


def rel_name(value: Any) -> str:
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def prefix_for(code: str) -> str:
    code = (code or "").strip().upper()
    if not code:
        return "MISSING"
    if code.startswith("BLQ-"):
        return "BLQ"
    if code.startswith("S."):
        return "SPAREX"
    if code.startswith("PAR-"):
        return "PAR"
    if "-" in code:
        return code.split("-", 1)[0]
    return "OTHER"


def main() -> int:
    db, uid, api_key, models = connect()
    domain = [("active", "=", True), ("sale_ok", "=", True), ("list_price", "<=", 1.0)]
    fields = ["id", "default_code", "name", "list_price", "standard_price", "categ_id", "public_categ_ids", "is_published", "website_published"]
    ids = execute(models, db, uid, api_key, "product.template", "search", [domain], {"context": {"active_test": False}})
    rows = []
    prefix_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    published_count = 0
    for offset in range(0, len(ids), 500):
        products = execute(models, db, uid, api_key, "product.template", "read", [ids[offset : offset + 500]], {"fields": fields, "context": {"active_test": False}})
        for product in products:
            code = product.get("default_code") or ""
            category = rel_name(product.get("categ_id"))
            published = bool(product.get("is_published")) or bool(product.get("website_published"))
            prefix = prefix_for(code)
            prefix_counts[prefix] += 1
            category_counts[category or "Uncategorized"] += 1
            if published:
                published_count += 1
            rows.append(
                {
                    "ID": product["id"],
                    "Internal Reference": code,
                    "Name": product.get("name") or "",
                    "Current Sales Price": product.get("list_price") or 0,
                    "Cost": product.get("standard_price") or 0,
                    "Internal Category": category,
                    "Prefix": prefix,
                    "Has Website Category": "Yes" if product.get("public_categ_ids") else "No",
                    "Published": "Yes" if published else "No",
                    "Search Query 1": f'"{code}" price',
                    "Search Query 2": f'"{code}" "{product.get("name") or ""}"',
                }
            )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    rows_path = OUT_DIR / f"placeholder_price_products_{stamp}.csv"
    summary_path = OUT_DIR / f"placeholder_price_profile_{stamp}.txt"
    with rows_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]) if rows else ["ID"])
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        f"Placeholder-price products: {len(rows)}",
        f"Published placeholder products: {published_count}",
        "",
        "By prefix:",
    ]
    lines.extend(f"{name}: {count}" for name, count in prefix_counts.most_common())
    lines.extend(["", "Top categories:"])
    lines.extend(f"{name}: {count}" for name, count in category_counts.most_common(30))
    summary_path.write_text("\n".join(lines), encoding="utf-8")
    print(summary_path.read_text(encoding="utf-8"))
    print(f"Rows: {rows_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

