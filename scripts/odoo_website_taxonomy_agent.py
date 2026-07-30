"""Plan Southern Equipment website product categories without writing to Odoo.

This script creates a customer-facing website taxonomy plan from the product
master and category audit files. It defaults to dry-run/report-only output.
Use --live-audit only to read existing Odoo public categories; no write path is
implemented here.
"""

from __future__ import annotations

import argparse
import csv
import os
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
MASTER_PRODUCTS = ROOT / "odoo_imports/product_master/import_ready/master_products.csv"
CATEGORY_AUDIT = ROOT / "odoo_imports/product_master/review_reports/odoo_category_existing_vs_needed_audit.csv"
REPORT_DIR = ROOT / "odoo_imports/product_master/review_reports"
DOC_DIR = ROOT / "odoo_imports/product_master/documentation"

SERVICE_TERMS = {
    "membership",
    "labor",
    "rental",
    "subscription",
    "diagnostic",
    "inspection",
    "freight",
    "delivery",
}

TOP_LEVEL_ORDER = [
    "Parts",
    "Filters",
    "Hydraulic",
    "Bearings",
    "Seals",
    "Hardware",
    "PTO & Driveline",
    "Engine",
    "Cooling",
    "Fuel System",
    "Electrical",
    "Lubricants",
    "Paint",
    "Shop Supplies",
    "Ground Engaging Tools",
    "Undercarriage",
    "Equipment & Attachments",
]


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\\", "/").split()).strip()


def lower_blob(*values: Any) -> str:
    return " ".join(clean_text(value).lower() for value in values if clean_text(value))


def title_path(*parts: str) -> str:
    return " / ".join(clean_text(part) for part in parts if clean_text(part))


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def is_service_or_hidden(row: dict[str, str]) -> tuple[bool, str]:
    code = clean_text(row.get("Internal Reference"))
    name = clean_text(row.get("Product Name") or row.get("Name"))
    family = clean_text(row.get("Product Family"))
    category = clean_text(row.get("Product Category") or row.get("category"))
    product_type = clean_text(row.get("Product Type") or row.get("type"))
    blob = lower_blob(code, name, family, category, product_type)

    if code == "SEC-MEMBERSHIP-STANDARD":
        return True, "Membership is a service, not a parts catalog item."
    if product_type.lower() == "service":
        return True, "Service product."
    if any(term in blob for term in SERVICE_TERMS):
        return True, "Service/rental/membership term detected."
    if "service" in lower_blob(category, product_type):
        return True, "Service category/type detected."
    return False, ""


def map_category(row: dict[str, str]) -> tuple[str, str, str]:
    """Return website category path, action, and notes."""
    hidden, reason = is_service_or_hidden(row)
    if hidden:
        return "", "Hide From Parts Catalog", reason

    internal = clean_text(row.get("Product Category") or row.get("category"))
    family = clean_text(row.get("Product Family"))
    name = clean_text(row.get("Product Name") or row.get("Name"))
    blob = lower_blob(internal, family, name)

    if "chain" in blob:
        return title_path("PTO & Driveline", "Roller Chain"), "Map", ""

    if "filter" in blob:
        if "air" in blob or "cab" in blob or "cabin" in blob:
            return title_path("Filters", "Air & Cabin Filters"), "Map", ""
        if "fuel water" in blob or "water separator" in blob:
            return title_path("Filters", "Fuel Water Separators"), "Map", ""
        if "fuel" in blob:
            return title_path("Filters", "Fuel Filters"), "Map", ""
        if "hydraulic" in blob or "hyd" in blob:
            return title_path("Filters", "Hydraulic Filters"), "Map", ""
        if "oil" in blob:
            return title_path("Filters", "Engine Oil Filters"), "Map", ""
        return title_path("Filters", "Other Filters"), "Map", "Generic filter; review subtype."

    if any(term in blob for term in ["grease", "fluid", "lubricant", "aw-", "80w", "hydraulic fluid"]):
        return title_path("Lubricants", "Oils, Fluids & Grease"), "Map", ""
    if "hydraulic" in blob or " jic" in blob or " npt" in blob or " bsp" in blob:
        if "hose" in blob:
            return title_path("Hydraulic", "Hoses"), "Map", ""
        if "coupler" in blob or "coupling" in blob:
            return title_path("Hydraulic", "Couplers"), "Map", ""
        if "tee" in blob:
            return title_path("Hydraulic", "Tees"), "Map", ""
        if "elbow" in blob or "90" in blob or "45" in blob:
            return title_path("Hydraulic", "Elbows"), "Map", ""
        if "plug" in blob:
            return title_path("Hydraulic", "Plugs"), "Map", ""
        if "cap" in blob:
            return title_path("Hydraulic", "Caps"), "Map", ""
        if "cylinder" in blob:
            return title_path("Hydraulic", "Cylinders"), "Map", ""
        return title_path("Hydraulic", "Adapters"), "Map", ""

    if "bearing" in blob or " brg" in blob:
        if "kit" in blob:
            return title_path("Bearings", "Bearing Kits"), "Map", ""
        if "wheel" in blob:
            return title_path("Bearings", "Wheel Bearings"), "Map", ""
        if "disc" in blob:
            return title_path("Bearings", "Disc Bearings"), "Map", ""
        return title_path("Bearings", "Ball & Roller Bearings"), "Map", ""

    if "seal" in blob or "o-ring" in blob or "oring" in blob:
        if "o-ring" in blob or "oring" in blob:
            return title_path("Seals", "O-Rings"), "Map", ""
        if "kit" in blob:
            return title_path("Seals", "Seal Kits"), "Map", ""
        if "wheel" in blob:
            return title_path("Seals", "Wheel Seals"), "Map", ""
        if "hydraulic" in blob or "rod" in blob or "piston" in blob:
            return title_path("Seals", "Hydraulic Seals"), "Map", ""
        return title_path("Seals", "Oil Seals"), "Map", ""

    if any(term in blob for term in ["bolt", "nut", "washer", "pin", "bushing", "clamp"]):
        if "hitch" in blob or "link" in blob:
            return title_path("PTO & Driveline", "Hitch & Linkage"), "Map", ""
        if "pin" in blob:
            return title_path("Hardware", "Pins"), "Map", ""
        if "bolt" in blob:
            return title_path("Hardware", "Bolts"), "Map", ""
        if "nut" in blob:
            return title_path("Hardware", "Nuts"), "Map", ""
        if "washer" in blob:
            return title_path("Hardware", "Washers"), "Map", ""
        return title_path("Hardware", "Miscellaneous Hardware"), "Map", ""

    if "pto" in blob or "u-joint" in blob or "u joint" in blob or "driveline" in blob or "yoke" in blob:
        if "u-joint" in blob or "u joint" in blob:
            return title_path("PTO & Driveline", "U-Joints"), "Map", ""
        if "yoke" in blob:
            return title_path("PTO & Driveline", "Yokes"), "Map", ""
        return title_path("PTO & Driveline", "PTO Parts"), "Map", ""

    if any(term in blob for term in ["engine", "gasket", "starter", "alternator", "thermostat", "water pump"]):
        if "gasket" in blob:
            return title_path("Engine", "Gaskets"), "Map", ""
        if "water pump" in blob or "thermostat" in blob:
            return title_path("Cooling", "Water Pumps & Thermostats"), "Map", ""
        if "starter" in blob or "alternator" in blob:
            return title_path("Electrical", "Starters & Alternators"), "Map", ""
        return title_path("Engine", "Engine Parts"), "Map", ""

    if any(term in blob for term in ["fuel", "pump", "injector", "tank cap", "fuel cap"]):
        return title_path("Fuel System", "Fuel System Parts"), "Map", ""

    if any(term in blob for term in ["relay", "switch", "sensor", "sender", "fuse", "glow plug", "electrical"]):
        return title_path("Electrical", "Electrical Parts"), "Map", ""

    if any(term in blob for term in ["oil", "grease", "fluid", "lubricant", "aw-", "80w"]):
        return title_path("Lubricants", "Oils, Fluids & Grease"), "Map", ""

    if "paint" in blob:
        return title_path("Paint", "Spray Paint"), "Map", ""

    if "undercarriage" in blob or "track" in blob:
        return title_path("Undercarriage", "Undercarriage Parts"), "Map", ""

    if "cutting edge" in blob or "bucket tooth" in blob or "ground engaging" in blob:
        return title_path("Ground Engaging Tools", "Cutting Edges & Teeth"), "Map", ""

    if "part" in blob or internal.startswith("Parts"):
        return title_path("Parts", "Miscellaneous Parts"), "Map", "Fallback parts category; review for better subtype."

    return "", "Review", "No clear parts website category."


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def live_public_categories() -> list[dict[str, Any]]:
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return models.execute_kw(
        db,
        uid,
        api_key,
        "product.public.category",
        "search_read",
        [[]],
        {"fields": ["id", "name", "parent_id", "website_id"], "limit": 1000, "order": "id asc"},
    )


def connect_odoo():
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def rel_name(value: Any) -> str:
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def live_products(limit: int = 0) -> list[dict[str, str]]:
    db, uid, api_key, models = connect_odoo()
    fields_get = models.execute_kw(db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    wanted = [
        "id",
        "name",
        "default_code",
        "list_price",
        "standard_price",
        "sale_ok",
        "active",
        "categ_id",
        "public_categ_ids",
    ]
    for optional in ["type", "detailed_type", "is_published", "website_published"]:
        if optional in fields_get:
            wanted.append(optional)
    domain = [("active", "=", True), ("sale_ok", "=", True)]
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        batch = models.execute_kw(
            db,
            uid,
            api_key,
            "product.template",
            "search_read",
            [domain],
            {
                "fields": wanted,
                "limit": min(1000, limit - len(records)) if limit and limit - len(records) < 1000 else 1000,
                "offset": offset,
                "context": {"active_test": False},
                "order": "default_code asc,id asc",
            },
        )
        if not batch:
            break
        records.extend(batch)
        if limit and len(records) >= limit:
            break
        offset += len(batch)

    rows: list[dict[str, str]] = []
    for record in records:
        product_type = clean_text(record.get("detailed_type") or record.get("type"))
        public_count = len(record.get("public_categ_ids") or [])
        is_published = bool(record.get("is_published")) or bool(record.get("website_published"))
        rows.append(
            {
                "External ID": str(record["id"]),
                "Internal Reference": clean_text(record.get("default_code")),
                "Product Name": clean_text(record.get("name")),
                "Product Family": "",
                "Product Category": rel_name(record.get("categ_id")),
                "Product Type": product_type,
                "Sales Price": str(record.get("list_price") or 0),
                "Cost": str(record.get("standard_price") or 0),
                "Website Category Count": str(public_count),
                "Published": "Yes" if is_published else "No",
            }
        )
    return rows


def build_public_category_rows(category_paths: Counter[str]) -> list[dict[str, Any]]:
    rows = []
    seen: set[str] = set()
    for path in sorted(category_paths):
        parts = [part.strip() for part in path.split("/") if part.strip()]
        parent = ""
        current_parts = []
        for part in parts:
            current_parts.append(part)
            current = title_path(*current_parts)
            if current in seen:
                parent = current
                continue
            rows.append(
                {
                    "Website Category": current,
                    "Name": part,
                    "Parent Category": parent,
                    "Product Count": category_paths.get(current, 0),
                    "Action": "Create/Verify",
                }
            )
            seen.add(current)
            parent = current
    return rows


def write_markdown(
    path: Path,
    timestamp: str,
    product_count: int,
    action_counts: Counter[str],
    category_counts: Counter[str],
    hidden_examples: list[dict[str, Any]],
    live_categories: list[dict[str, Any]],
) -> None:
    top_categories = category_counts.most_common(25)
    lines = [
        "# Southern Equipment Website Parts Taxonomy Plan",
        "",
        f"Generated: {timestamp}",
        "",
        "## Purpose",
        "",
        "Make the Odoo eCommerce catalog browse like a professional parts catalog instead of exposing raw internal/service categories.",
        "",
        "## Recommended Top-Level Website Categories",
        "",
    ]
    lines.extend(f"- {category}" for category in TOP_LEVEL_ORDER)
    lines.extend(
        [
            "",
            "## Dry-Run Summary",
            "",
            f"- Products reviewed: {product_count}",
            f"- Products mapped to website categories: {action_counts.get('Map', 0)}",
            f"- Products hidden from parts catalog: {action_counts.get('Hide From Parts Catalog', 0)}",
            f"- Products needing review: {action_counts.get('Review', 0)}",
            "",
            "## Highest-Volume Website Categories",
            "",
        ]
    )
    lines.extend(f"- {category}: {count}" for category, count in top_categories)
    lines.extend(
        [
            "",
            "## Hide From Parts Catalog",
            "",
            "Services, memberships, subscriptions, labor, rental, freight, and diagnostic products should not live under the parts browse tree. They can stay published in their own website/service area if desired, but they should not be assigned to public parts categories.",
            "",
        ]
    )
    if hidden_examples:
        lines.extend(
            f"- {row.get('Internal Reference', '')}: {row.get('Product Name', '')} ({row.get('Notes', '')})"
            for row in hidden_examples[:20]
        )
    else:
        lines.append("- No hidden examples found in the source file.")
    lines.extend(
        [
            "",
            "## Odoo Import Strategy",
            "",
            "1. Create or verify public website categories first using the category plan CSV.",
            "2. Import product public-category assignments only after a small staging test batch.",
            "3. Keep internal categories such as `Parts / Hydraulic / Hydraulic Adapters` for accounting/reporting.",
            "4. Use public categories such as `Hydraulic / Adapters` for customer browsing.",
            "5. Keep `Standard Membership` as a service and out of the parts catalog.",
            "",
            "## Staging Test Steps",
            "",
            "1. Install/verify website_sale on staging.",
            "2. Import or manually create the top-level public categories.",
            "3. Test 25 products across Hydraulic, Filters, Hardware, PTO & Driveline, and Lubricants.",
            "4. Confirm product pages show the expected public category breadcrumb.",
            "5. Confirm Membership is not visible in the parts browse tree.",
            "6. Only then run the apply/publish workflow in production.",
            "",
            "## Live Public Category Audit",
            "",
        ]
    )
    if live_categories:
        lines.append(f"- Live public categories read: {len(live_categories)}")
        visible_membership = [row for row in live_categories if "membership" in clean_text(row.get("name")).lower()]
        if visible_membership:
            lines.append("- Membership category exists live and should be removed from parts products or moved to a service area.")
    else:
        lines.append("- Not run. Use `--live-audit` for a read-only Odoo category pull.")
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a dry-run website taxonomy plan for Southern parts.")
    parser.add_argument("--master-csv", type=Path, default=MASTER_PRODUCTS)
    parser.add_argument("--category-audit-csv", type=Path, default=CATEGORY_AUDIT)
    parser.add_argument("--live-products", action="store_true", help="Read active saleable products from Odoo. No writes.")
    parser.add_argument("--live-audit", action="store_true", help="Read live Odoo public categories. No writes.")
    parser.add_argument("--limit", type=int, default=0, help="Limit live product reads. Use 0 for no limit.")
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    products = live_products(args.limit) if args.live_products else read_csv(args.master_csv)
    category_audit = read_csv(args.category_audit_csv)

    mapping_rows: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    action_counts: Counter[str] = Counter()
    internal_counts: Counter[str] = Counter()
    hidden_examples: list[dict[str, Any]] = []

    for row in products:
        product_id = clean_text(row.get("External ID"))
        code = clean_text(row.get("Internal Reference"))
        name = clean_text(row.get("Product Name"))
        internal_category = clean_text(row.get("Product Category"))
        family = clean_text(row.get("Product Family"))
        public_category, action, notes = map_category(row)
        internal_counts[internal_category] += 1
        action_counts[action] += 1
        if public_category:
            category_counts[public_category] += 1
        out = {
            "External ID": product_id,
            "Internal Reference": code,
            "Product Name": name,
            "Product Family": family,
            "Internal Category": internal_category,
            "Recommended Website Category": public_category,
            "Action": action,
            "Sales Price": clean_text(row.get("Sales Price")),
            "Current Website Category Count": clean_text(row.get("Website Category Count")),
            "Published": clean_text(row.get("Published")),
            "Notes": notes,
        }
        mapping_rows.append(out)
        if action == "Hide From Parts Catalog":
            hidden_examples.append(out)

    category_rows = build_public_category_rows(category_counts)
    audit_summary_rows = [
        {
            "Internal Category": row.get("Display Name", ""),
            "Existing Audit Action": row.get("Action", ""),
            "Existing Audit Product Count": row.get("Product Count", ""),
            "Observed Master Product Count": internal_counts.get(row.get("Display Name", ""), 0),
        }
        for row in category_audit
    ]

    live_categories = live_public_categories() if args.live_audit else []

    mapping_path = REPORT_DIR / f"website_category_product_mapping_dry_run_{timestamp}.csv"
    category_path = REPORT_DIR / f"website_public_category_create_plan_{timestamp}.csv"
    audit_path = REPORT_DIR / f"website_internal_category_audit_summary_{timestamp}.csv"
    doc_path = DOC_DIR / "website_parts_taxonomy_plan.md"

    write_csv(
        mapping_path,
        mapping_rows,
        [
            "External ID",
            "Internal Reference",
            "Product Name",
            "Product Family",
            "Internal Category",
            "Recommended Website Category",
            "Action",
            "Sales Price",
            "Current Website Category Count",
            "Published",
            "Notes",
        ],
    )
    write_csv(category_path, category_rows, ["Website Category", "Name", "Parent Category", "Product Count", "Action"])
    write_csv(audit_path, audit_summary_rows, ["Internal Category", "Existing Audit Action", "Existing Audit Product Count", "Observed Master Product Count"])
    write_markdown(doc_path, timestamp, len(products), action_counts, category_counts, hidden_examples, live_categories)

    print(
        {
            "mode": "dry_run",
            "products_reviewed": len(products),
            "mapped": action_counts.get("Map", 0),
            "hidden": action_counts.get("Hide From Parts Catalog", 0),
            "review": action_counts.get("Review", 0),
            "mapping_csv": str(mapping_path),
            "category_plan_csv": str(category_path),
            "audit_summary_csv": str(audit_path),
            "taxonomy_doc": str(doc_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


