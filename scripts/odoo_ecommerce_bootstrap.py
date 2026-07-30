from __future__ import annotations

import argparse
import csv
import html
import os
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT = ROOT / "odoo_imports/product_master/review_reports/odoo_ecommerce_bootstrap_plan.csv"

REQUIRED_MODULES = [
    "website",
    "website_sale",
    "website_sale_stock",
    "sale_management",
    "payment",
]


def load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}. Copy odoo_connection.env.example and fill it in.")
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


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def rel_name(value) -> str:
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def clean_text(value) -> str:
    return " ".join(str(value or "").split())


def connect():
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed. Check ODOO_URL, ODOO_DB, ODOO_USERNAME, and ODOO_API_KEY.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return url, db, uid, api_key, models


def module_states(models, db, uid, api_key) -> dict[str, str]:
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.module.module",
        "search_read",
        [[("name", "in", REQUIRED_MODULES)]],
        {"fields": ["name", "state"], "limit": len(REQUIRED_MODULES) + 5},
    )
    return {row["name"]: row["state"] for row in rows}


def select_website(models, db, uid, api_key, requested: str | None) -> dict:
    websites = execute(
        models,
        db,
        uid,
        api_key,
        "website",
        "search_read",
        [[]],
        {"fields": ["id", "name"], "limit": 100, "order": "id asc"},
    )
    if not websites:
        raise SystemExit("No websites found in Odoo.")
    if requested:
        requested_lower = requested.lower()
        matches = [row for row in websites if requested_lower in row["name"].lower()]
        if not matches:
            names = ", ".join(row["name"] for row in websites)
            raise SystemExit(f"No website matched {requested!r}. Available websites: {names}")
        return matches[0]
    southern = [row for row in websites if "southern" in row["name"].lower()]
    return southern[0] if southern else websites[0]


def get_public_category(models, db, uid, api_key, name: str, parent_id: int | None, website_id: int):
    domain = [("name", "=", name), ("website_id", "=", website_id)]
    domain.append(("parent_id", "=", parent_id if parent_id else False))
    ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.public.category",
        "search",
        [domain],
        {"limit": 1},
    )
    return ids[0] if ids else None


def ensure_public_category_path(models, db, uid, api_key, path: str, website_id: int, apply: bool):
    parent_id = None
    created = []
    existing = []
    for part in [clean_text(item) for item in path.split("/") if clean_text(item)]:
        category_id = get_public_category(models, db, uid, api_key, part, parent_id, website_id)
        if category_id:
            existing.append(part)
            parent_id = category_id
            continue
        if not apply:
            created.append(part)
            parent_id = None
            continue
        values = {"name": part, "website_id": website_id}
        if parent_id:
            values["parent_id"] = parent_id
        category_id = execute(
            models,
            db,
            uid,
            api_key,
            "product.public.category",
            "create",
            [values],
        )
        created.append(part)
        parent_id = category_id
    return parent_id, existing, created


def product_description(product: dict) -> str:
    code = clean_text(product.get("default_code"))
    name = clean_text(product.get("name"))
    category = rel_name(product.get("categ_id"))
    manufacturer = clean_text(product.get("x_studio_manufacturer"))
    bits = [html.escape(name)]
    if code:
        bits.append(f"Part number: {html.escape(code)}.")
    if manufacturer:
        bits.append(f"Manufacturer: {html.escape(manufacturer)}.")
    if category:
        bits.append(f"Category: {html.escape(category)}.")
    return "<p>" + " ".join(bits) + "</p>"


def product_domain(args) -> list:
    domain = [("active", "=", True), ("sale_ok", "=", True)]
    if not args.all_saleable:
        roots = tuple(args.root_category)
        if roots:
            domain.append(("categ_id", "child_of", category_ids_for_roots(args, roots)))
    if args.only_unpublished:
        domain.append(("website_published", "=", False))
    return domain


def category_ids_for_roots(args, roots: tuple[str, ...]) -> list[int]:
    ids = []
    for root in roots:
        matched = args._models.execute_kw(
            args._db,
            args._uid,
            args._api_key,
            "product.category",
            "search",
            [[("name", "=", root)]],
            {"limit": 20},
        )
        ids.extend(matched)
    if not ids:
        raise SystemExit(f"No internal product categories matched: {', '.join(roots)}")
    return ids


def should_skip(product: dict) -> str:
    if not clean_text(product.get("default_code")):
        return "missing internal reference"
    if not clean_text(product.get("name")):
        return "missing product name"
    if not rel_name(product.get("categ_id")):
        return "missing internal category"
    if float(product.get("list_price") or 0) <= 0:
        return "missing sales price"
    return ""


def write_report(rows: list[dict]) -> None:
    fields = [
        "Timestamp",
        "Mode",
        "Product ID",
        "Internal Reference",
        "Product Name",
        "Internal Category",
        "Website",
        "Website Category",
        "Action",
        "Published",
        "Product URL",
        "Notes",
    ]
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    with REPORT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare saleable Odoo products for website_sale ecommerce.",
    )
    parser.add_argument("--apply", action="store_true", help="Create website categories and update products.")
    parser.add_argument("--publish", action="store_true", help="Publish products too. Requires --apply.")
    parser.add_argument("--limit", type=int, default=100, help="Maximum products to process. Use 0 for no limit.")
    parser.add_argument("--website-name", help="Substring of the target Odoo website name.")
    parser.add_argument(
        "--root-category",
        action="append",
        default=None,
        help="Internal product category root to include. Repeatable. Defaults to Parts.",
    )
    parser.add_argument("--all-saleable", action="store_true", help="Ignore root categories and include every saleable product.")
    parser.add_argument("--include-published", dest="only_unpublished", action="store_false", help="Include already-published products.")
    parser.set_defaults(only_unpublished=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    if args.root_category is None:
        args.root_category = ["Parts"]
    if args.publish and not args.apply:
        raise SystemExit("--publish requires --apply.")

    _, db, uid, api_key, models = connect()
    args._db = db
    args._uid = uid
    args._api_key = api_key
    args._models = models

    states = module_states(models, db, uid, api_key)
    missing = [name for name in REQUIRED_MODULES if states.get(name) != "installed"]
    if missing:
        detail = ", ".join(f"{name}={states.get(name, 'missing')}" for name in missing)
        raise SystemExit(f"Required ecommerce modules are not installed: {detail}")

    website = select_website(models, db, uid, api_key, args.website_name)
    fields = [
        "id",
        "default_code",
        "name",
        "active",
        "sale_ok",
        "list_price",
        "categ_id",
        "public_categ_ids",
        "website_id",
        "website_published",
        "is_published",
        "website_url",
        "website_absolute_url",
        "description_ecommerce",
        "website_description",
        "website_meta_title",
        "website_meta_description",
    ]
    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    if "x_studio_manufacturer" in product_fields:
        fields.append("x_studio_manufacturer")

    kwargs = {"fields": fields, "order": "default_code asc,id asc", "context": {"active_test": False}}
    if args.limit > 0:
        kwargs["limit"] = args.limit
    products = execute(models, db, uid, api_key, "product.template", "search_read", [product_domain(args)], kwargs)

    rows = []
    updated = 0
    skipped = 0
    timestamp = datetime.now().isoformat(timespec="seconds")
    mode = "APPLY" if args.apply else "DRY RUN"

    for product in products:
        note = should_skip(product)
        category_path = rel_name(product.get("categ_id"))
        product_url = product.get("website_absolute_url") or product.get("website_url") or ""
        if note:
            skipped += 1
            rows.append(
                {
                    "Timestamp": timestamp,
                    "Mode": mode,
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code", ""),
                    "Product Name": product.get("name", ""),
                    "Internal Category": category_path,
                    "Website": website["name"],
                    "Website Category": "",
                    "Action": "SKIPPED",
                    "Published": "No",
                    "Product URL": product_url,
                    "Notes": note,
                }
            )
            continue

        public_category_id, existing_parts, created_parts = ensure_public_category_path(
            models,
            db,
            uid,
            api_key,
            category_path,
            website["id"],
            args.apply,
        )
        values = {}
        if public_category_id:
            values["public_categ_ids"] = [(6, 0, [public_category_id])]
        values["website_id"] = website["id"]
        if not product.get("description_ecommerce"):
            values["description_ecommerce"] = product_description(product)
        if not product.get("website_description"):
            values["website_description"] = product_description(product)
        if not product.get("website_meta_title"):
            values["website_meta_title"] = clean_text(product.get("name"))
        if not product.get("website_meta_description"):
            code = clean_text(product.get("default_code"))
            name = clean_text(product.get("name"))
            values["website_meta_description"] = f"{name}. Part number {code}. Available from Southern Equipment."
        if args.publish:
            values["is_published"] = True
            values["website_published"] = True

        if args.apply and values:
            execute(models, db, uid, api_key, "product.template", "write", [[product["id"]], values])
            updated += 1

        action_bits = []
        if created_parts:
            action_bits.append("create website categories")
        if values:
            action_bits.append("update product website fields")
        if args.publish:
            action_bits.append("publish")
        action = ", ".join(action_bits) if action_bits else "NO CHANGE"
        notes = []
        if existing_parts:
            notes.append("existing category path: " + " / ".join(existing_parts))
        if created_parts:
            notes.append(("created" if args.apply else "would create") + ": " + " / ".join(created_parts))

        rows.append(
            {
                "Timestamp": timestamp,
                "Mode": mode,
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code", ""),
                "Product Name": product.get("name", ""),
                "Internal Category": category_path,
                "Website": website["name"],
                "Website Category": category_path,
                "Action": action,
                "Published": "Yes" if args.publish else ("Yes" if product.get("website_published") else "No"),
                "Product URL": product_url,
                "Notes": "; ".join(notes),
            }
        )

    write_report(rows)
    print(f"Connected uid: {uid}")
    print("Required ecommerce modules: installed")
    print(f"Website: {website['name']} (id={website['id']})")
    print(f"Mode: {mode}")
    print(f"Products reviewed: {len(products)}")
    print(f"Products skipped: {skipped}")
    if args.apply:
        print(f"Products updated: {updated}")
    else:
        planned = sum(1 for row in rows if row["Action"] not in {"SKIPPED", "NO CHANGE"})
        print(f"Products with planned updates: {planned}")
    print(f"Report: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
