from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SPAREX_DIR = ROOT / "odoo_imports" / "product_master" / "sparex"
PRICING_DIR = SPAREX_DIR / "pricing"


FIELDNAMES = [
    "Internal Reference",
    "Name",
    "Category",
    "Current Cost/Vendor Price",
    "Current Odoo Sales Price",
    "Proposed Retail Price",
    "Pricing Method",
    "Confidence",
    "Evidence URLs",
    "Notes",
]


PRICE_TIERS = [
    (0.01, 4.99, 0.60),
    (5.00, 24.99, 0.55),
    (25.00, 99.99, 0.45),
    (100.00, 499.99, 0.38),
    (500.00, 999999.99, 0.30),
]


CATEGORY_MARGIN_OVERRIDES = [
    (re.compile(r"hardware|fastener|pin|bolt|washer|nut", re.I), 0.58),
    (re.compile(r"filter", re.I), 0.45),
    (re.compile(r"hydraulic", re.I), 0.45),
    (re.compile(r"electrical|electrics|switch|sensor", re.I), 0.42),
    (re.compile(r"cab|sheet metal|engine|pto|driveline|power transmission|axle", re.I), 0.38),
    (re.compile(r"workshop|tool|merchandising", re.I), 0.35),
]


SERVICE_PATTERN = re.compile(r"\b(membership|subscription|service plan|labor|labour)\b", re.I)


def load_env() -> None:
    if not ENV_PATH.exists():
        return
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


def clean_sku(value: Any) -> str:
    text = str(value or "").strip().upper()
    match = re.search(r"S\.\s*(\d+)", text)
    if match:
        return f"S.{match.group(1)}"
    return text


def money(value: Any) -> float:
    if value is None or value is False:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = re.sub(r"[^0-9.\-]", "", str(value))
    if not text:
        return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


def first_text(*values: Any) -> str:
    for value in values:
        if value:
            return str(value).strip()
    return ""


def harvest_items_from_file(path: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("items", "products", "results", "records"):
            rows = data.get(key)
            if isinstance(rows, list):
                return [item for item in rows if isinstance(item, dict)]
    return []


def extract_harvest_record(raw: dict[str, Any], path: Path) -> dict[str, Any] | None:
    product = raw.get("product") if isinstance(raw.get("product"), dict) else {}
    source = raw.get("source") if isinstance(raw.get("source"), dict) else {}
    sku = clean_sku(
        first_text(
            raw.get("sku"),
            raw.get("vendor_code"),
            raw.get("internal_reference"),
            raw.get("Internal Reference"),
            product.get("sku"),
            product.get("vendor_code"),
        )
    )
    if not sku.startswith("S."):
        return None
    return {
        "sku": sku,
        "name": first_text(raw.get("name"), product.get("name"), raw.get("title")),
        "category": first_text(raw.get("odoo_category"), raw.get("category"), product.get("category")),
        "source_url": first_text(raw.get("source_url"), raw.get("url"), source.get("url"), product.get("url")),
        "vendor_price": money(first_text(raw.get("vendor_price"), raw.get("price"), product.get("vendor_price"), product.get("price"))),
        "source_file": str(path),
    }


def load_harvest_records() -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for path in sorted(SPAREX_DIR.glob("*.json"), key=lambda item: item.stat().st_mtime):
        if "summary" in path.name.lower() or "targets" in path.name.lower():
            continue
        for raw in harvest_items_from_file(path):
            record = extract_harvest_record(raw, path)
            if not record:
                continue
            existing = records.get(record["sku"], {})
            records[record["sku"]] = {
                **existing,
                **{key: value for key, value in record.items() if value not in ("", 0.0)},
            }
    return records


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def connect_odoo():
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return models, db, uid, api_key


def execute(models, db: str, uid: int, api_key: str, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def load_odoo_products(limit: int = 0) -> dict[str, dict[str, Any]]:
    models, db, uid, api_key = connect_odoo()
    domain = [("default_code", "=like", "S.%")]
    search_kwargs: dict[str, Any] = {"order": "default_code"}
    if limit:
        search_kwargs["limit"] = limit
    ids = execute(models, db, uid, api_key, "product.template", "search", [domain], search_kwargs)
    available_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["string"]})
    wanted_fields = ["id", "default_code", "name", "categ_id", "standard_price", "list_price", "active", "sale_ok", "type", "detailed_type"]
    fields = [field for field in wanted_fields if field in available_fields]
    rows: list[dict[str, Any]] = []
    for id_chunk in chunks(ids, 500):
        rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [id_chunk],
                {"fields": fields, "context": {"active_test": False}},
            )
        )
    supplier_rows: list[dict[str, Any]] = []
    for id_chunk in chunks([row["id"] for row in rows], 500):
        supplier_rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.supplierinfo",
                "search_read",
                [[("product_tmpl_id", "in", id_chunk)]],
                {"fields": ["product_tmpl_id", "product_code", "price"]},
            )
        )
    supplier_by_product: dict[int, float] = {}
    for row in supplier_rows:
        product = row.get("product_tmpl_id")
        if not product:
            continue
        product_id = int(product[0])
        price = money(row.get("price"))
        if price > 0 and (product_id not in supplier_by_product or price < supplier_by_product[product_id]):
            supplier_by_product[product_id] = price

    by_sku: dict[str, dict[str, Any]] = {}
    for row in rows:
        sku = clean_sku(row.get("default_code"))
        if not sku:
            continue
        category = ""
        if isinstance(row.get("categ_id"), list) and len(row["categ_id"]) > 1:
            category = str(row["categ_id"][1])
        by_sku[sku] = {
            "sku": sku,
            "name": row.get("name") or "",
            "category": category,
            "standard_price": money(row.get("standard_price")),
            "list_price": money(row.get("list_price")),
            "supplier_price": supplier_by_product.get(int(row["id"]), 0.0),
            "active": bool(row.get("active")),
            "sale_ok": bool(row.get("sale_ok")),
            "type": row.get("type") or row.get("detailed_type") or "",
        }
    return by_sku


def base_margin_for(cost: float, category: str) -> float:
    for pattern, margin in CATEGORY_MARGIN_OVERRIDES:
        if pattern.search(category):
            return margin
    for low, high, margin in PRICE_TIERS:
        if low <= cost <= high:
            return margin
    return 0.38


def round_retail(value: float) -> float:
    if value <= 0:
        return 0.0
    if value < 10:
        return round(math.ceil(value * 2) / 2 - 0.01, 2)
    if value < 100:
        return round(math.ceil(value) - 0.01, 2)
    if value < 500:
        return round(math.ceil(value / 5) * 5 - 0.01, 2)
    return round(math.ceil(value / 10) * 10 - 0.01, 2)


def choose_cost(odoo: dict[str, Any], harvest: dict[str, Any]) -> tuple[float, str, str]:
    standard_price = money(odoo.get("standard_price"))
    supplier_price = money(odoo.get("supplier_price"))
    harvest_price = money(harvest.get("vendor_price"))
    if standard_price > 0:
        return standard_price, "Odoo standard cost", "medium"
    if supplier_price > 0:
        return supplier_price, "Odoo supplier/vendor price", "medium"
    if harvest_price > 0:
        return harvest_price, "Sparex harvested membership price", "low"
    return 0.0, "No usable cost", "none"


def proposal_for(sku: str, odoo: dict[str, Any], harvest: dict[str, Any]) -> dict[str, Any]:
    name = first_text(odoo.get("name"), harvest.get("name"))
    category = first_text(odoo.get("category"), harvest.get("category"))
    source_url = first_text(harvest.get("source_url"))
    current_list = money(odoo.get("list_price"))
    cost, cost_source, cost_quality = choose_cost(odoo, harvest)

    if SERVICE_PATTERN.search(name) or SERVICE_PATTERN.search(category):
        return {
            "Internal Reference": sku,
            "Name": name,
            "Category": category,
            "Current Cost/Vendor Price": f"{cost:.2f}" if cost else "",
            "Current Odoo Sales Price": f"{current_list:.2f}" if current_list else "",
            "Proposed Retail Price": "",
            "Pricing Method": "Needs Review - service item",
            "Confidence": "Review",
            "Evidence URLs": source_url,
            "Notes": "Do not price as a stock good. Standard membership/subscription/service products should be handled as services.",
        }

    if cost <= 0:
        return {
            "Internal Reference": sku,
            "Name": name,
            "Category": category,
            "Current Cost/Vendor Price": "",
            "Current Odoo Sales Price": f"{current_list:.2f}" if current_list else "",
            "Proposed Retail Price": "",
            "Pricing Method": "Needs Review - missing usable cost",
            "Confidence": "Review",
            "Evidence URLs": source_url,
            "Notes": "No reliable cost/vendor price found. Do not use Standard Sparex membership price as retail. Research exact SKU/OEM market price or load supplier price feed first.",
        }

    margin = base_margin_for(cost, category)
    proposed = round_retail(cost / (1.0 - margin))
    confidence = "Medium" if cost_quality == "medium" else "Low"
    notes = f"Cost source: {cost_source}. Matrix target gross margin: {margin:.0%}."
    if cost_quality == "low":
        notes += " Sparex membership pricing is a weak cost signal and should be reviewed before import."
    if current_list and abs(current_list - proposed) / max(proposed, 0.01) > 0.35:
        notes += f" Current Odoo sales price differs from proposal by more than 35%."

    return {
        "Internal Reference": sku,
        "Name": name,
        "Category": category,
        "Current Cost/Vendor Price": f"{cost:.2f}",
        "Current Odoo Sales Price": f"{current_list:.2f}" if current_list else "",
        "Proposed Retail Price": f"{proposed:.2f}",
        "Pricing Method": "Category margin matrix from usable cost",
        "Confidence": confidence,
        "Evidence URLs": source_url,
        "Notes": notes,
    }


def write_rules(path: Path) -> None:
    rules = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "source_priority": [
            "Exact SKU/OEM competitor match",
            "Supplier/dealer price feed",
            "Category margin rule from known cost",
            "Needs Review",
        ],
        "standard_sparex_membership_pricing": "Unsuitable as retail; weak data point only.",
        "cost_tiers": [
            {"cost_min": low, "cost_max": high, "target_gross_margin": margin}
            for low, high, margin in PRICE_TIERS
        ],
        "category_overrides": [
            {"category_pattern": pattern.pattern, "target_gross_margin": margin}
            for pattern, margin in CATEGORY_MARGIN_OVERRIDES
        ],
        "rounding": {
            "under_10": "next $0.50 ending in .49 or .99",
            "10_to_99": "next whole dollar ending in .99",
            "100_to_499": "next $5 ending in .99",
            "500_plus": "next $10 ending in .99",
        },
    }
    path.write_text(json.dumps(rules, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a read-only Sparex retail pricing proposal CSV. Does not write to Odoo.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max Odoo Sparex products to process.")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    PRICING_DIR.mkdir(parents=True, exist_ok=True)
    harvest = load_harvest_records()
    odoo_products = load_odoo_products(limit=args.limit)
    skus = sorted(set(harvest) | set(odoo_products))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output = args.output or PRICING_DIR / f"sparex_retail_price_proposals_{timestamp}.csv"
    rows = [proposal_for(sku, odoo_products.get(sku, {}), harvest.get(sku, {})) for sku in skus]

    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    rules_path = PRICING_DIR / "sparex_pricing_rules.json"
    write_rules(rules_path)

    proposed = sum(1 for row in rows if row["Proposed Retail Price"])
    review = len(rows) - proposed
    medium = sum(1 for row in rows if row["Confidence"] == "Medium")
    low = sum(1 for row in rows if row["Confidence"] == "Low")

    print(f"Output: {output}")
    print(f"Rules: {rules_path}")
    print(f"Rows: {len(rows)}")
    print(f"Proposed prices: {proposed}")
    print(f"Needs review: {review}")
    print(f"Medium confidence: {medium}")
    print(f"Low confidence: {low}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
