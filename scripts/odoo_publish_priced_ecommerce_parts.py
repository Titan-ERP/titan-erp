from __future__ import annotations

import argparse
import csv
import os
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"


def load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}")
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


def connect():
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def select_website(models, db, uid, api_key, requested: str | None) -> dict[str, Any]:
    websites = execute(models, db, uid, api_key, "website", "search_read", [[]], {"fields": ["id", "name"], "limit": 100, "order": "id asc"})
    if not websites:
        raise SystemExit("No website records found.")
    if requested:
        matches = [website for website in websites if requested.lower() in website["name"].lower()]
        if matches:
            return matches[0]
    southern = [website for website in websites if "southern" in website["name"].lower()]
    return southern[0] if southern else websites[0]


def find_public_category(
    models,
    db,
    uid,
    api_key,
    name: str,
    parent_id: int | None,
    website_id: int,
    cache: dict[tuple[str, int | None], int | None],
) -> int | None:
    cache_key = (name, parent_id)
    if cache_key in cache:
        return cache[cache_key]
    domains = [
        [("name", "=", name), ("parent_id", "=", parent_id or False), ("website_id", "=", website_id)],
        [("name", "=", name), ("parent_id", "=", parent_id or False), ("website_id", "=", False)],
    ]
    for domain in domains:
        ids = execute(models, db, uid, api_key, "product.public.category", "search", [domain], {"limit": 1})
        if ids:
            cache[cache_key] = ids[0]
            return ids[0]
    cache[cache_key] = None
    return None


def ensure_public_category_path(
    models,
    db,
    uid,
    api_key,
    complete_name: str,
    website_id: int,
    apply: bool,
    cache: dict[tuple[str, int | None], int | None],
    path_cache: dict[str, int | None],
) -> int | None:
    complete_name = clean_text(complete_name)
    if complete_name in path_cache:
        return path_cache[complete_name]
    parent_id = None
    parts = [clean_text(part) for part in complete_name.split("/") if clean_text(part)]
    if not parts:
        return None
    for part in parts:
        category_id = find_public_category(models, db, uid, api_key, part, parent_id, website_id, cache)
        if category_id:
            parent_id = category_id
            continue
        if not apply:
            path_cache[complete_name] = None
            return None
        values: dict[str, Any] = {"name": part, "website_id": website_id}
        if parent_id:
            values["parent_id"] = parent_id
        parent_id = execute(models, db, uid, api_key, "product.public.category", "create", [values])
        cache[(part, values.get("parent_id"))] = parent_id
    path_cache[complete_name] = parent_id
    return parent_id


def apply_price_proposals(models, db, uid, api_key, proposal_csv: Path, apply: bool) -> list[dict[str, Any]]:
    if not proposal_csv.exists():
        return []
    rows = list(csv.DictReader(proposal_csv.open(encoding="utf-8-sig")))
    results = []
    for row in rows:
        if row.get("Status") != "Ready For Review":
            continue
        product_id = int(row["ID"]) if row.get("ID") else 0
        proposed = float(row["Proposed Sales Price"]) if row.get("Proposed Sales Price") else 0.0
        if not product_id or proposed <= 1.0:
            continue
        if apply:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"list_price": proposed}])
        results.append(
            {
                "Product ID": product_id,
                "Internal Reference": row.get("Internal Reference", ""),
                "Name": row.get("Name", ""),
                "Action": "Price Updated" if apply else "Would Update Price",
                "Old Price": row.get("Current Sales Price", ""),
                "New Price": proposed,
                "Notes": f"Source(s): {row.get('Sources', '')}",
            }
        )
    return results


def publish_priced_parts(models, db, uid, api_key, website_id: int, apply: bool, publish: bool, limit: int) -> list[dict[str, Any]]:
    fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    publish_values: dict[str, Any] = {"sale_ok": True}
    if "website_published" in fields:
        publish_values["website_published"] = True
    if "is_published" in fields:
        publish_values["is_published"] = True
    if not any(field in publish_values for field in ["website_published", "is_published"]):
        raise SystemExit("No product publish field found.")

    domain = [
        ("active", "=", True),
        ("sale_ok", "=", True),
        ("type", "!=", "service"),
        ("list_price", ">", 1.0),
        ("categ_id.complete_name", "=ilike", "Parts%"),
    ]
    product_ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [domain],
        {"context": {"active_test": False}, "limit": limit or 0},
    )
    report_rows = []
    public_category_cache: dict[tuple[str, int | None], int | None] = {}
    public_category_path_cache: dict[str, int | None] = {}
    for id_chunk in chunks(product_ids, 250):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [id_chunk],
            {
                "fields": [
                    "id",
                    "default_code",
                    "name",
                    "list_price",
                    "categ_id",
                    "public_categ_ids",
                    "website_published",
                    "is_published",
                ],
                "context": {"active_test": False},
            },
        )
        write_ids = []
        category_writes: list[tuple[int, int]] = []
        for product in products:
            internal_category = product["categ_id"][1] if isinstance(product.get("categ_id"), list) else ""
            public_category_id = ensure_public_category_path(
                models,
                db,
                uid,
                api_key,
                internal_category,
                website_id,
                apply,
                public_category_cache,
                public_category_path_cache,
            )
            already_published = bool(product.get("website_published")) or bool(product.get("is_published"))
            if public_category_id and public_category_id not in (product.get("public_categ_ids") or []):
                category_writes.append((product["id"], public_category_id))
            if publish and not already_published:
                write_ids.append(product["id"])
            report_rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Action": (
                        "Published"
                        if publish and not already_published
                        else "Already Published"
                        if already_published
                        else "Category Only"
                    ),
                    "Old Price": "",
                    "New Price": product.get("list_price") or "",
                    "Notes": f"Website category: {internal_category}",
                }
            )
        if apply:
            for product_id, public_category_id in category_writes:
                execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"public_categ_ids": [(4, public_category_id)]}])
            if publish and write_ids:
                execute(models, db, uid, api_key, "product.template", "write", [write_ids, publish_values])
    return report_rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply reviewed retail price proposals and publish priced parts to Odoo eCommerce.")
    parser.add_argument("--proposal-csv", type=Path, default=REPORT_DIR / "retail_price_research_initial_20260725_odoo_price_proposals.csv")
    parser.add_argument("--website-name", default="Southern")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true", help="Publish eligible products too. Requires --apply.")
    args = parser.parse_args()
    if args.publish and not args.apply:
        raise SystemExit("--publish requires --apply.")

    db, uid, api_key, models = connect()
    website = select_website(models, db, uid, api_key, args.website_name)

    price_rows = apply_price_proposals(models, db, uid, api_key, args.proposal_csv, args.apply)
    publish_rows = publish_priced_parts(
        models,
        db,
        uid,
        api_key,
        website["id"],
        args.apply,
        args.publish,
        args.limit,
    )
    all_rows = price_rows + publish_rows

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / f"odoo_publish_priced_parts_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    fieldnames = ["Product ID", "Internal Reference", "Name", "Action", "Old Price", "New Price", "Notes"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(all_rows)

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"Website: {website['name']} ({website['id']})")
    print(f"Price proposal updates: {len(price_rows)}")
    print(f"Priced parts publish candidates: {len(publish_rows)}")
    print(f"Report: {path}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
