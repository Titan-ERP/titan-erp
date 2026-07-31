import csv
import os
import xmlrpc.client
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SAFE_IMPORT = ROOT / "odoo_imports" / "product_master" / "import_ready" / "odoo_exact_schema_safe_import.csv"
ARCHIVE_IMPORT = ROOT / "odoo_imports" / "product_master" / "import_ready" / "odoo_archive_products_set_inactive.csv"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def connect():
    load_env(ENV_PATH)
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def split_external_id(external_id):
    if "." not in external_id:
        return None
    module, name = external_id.split(".", 1)
    if not module or not name:
        return None
    return module, name


def resolve_external_ids(models, db, uid, api_key, model_name, external_ids):
    parts = [split_external_id(external_id) for external_id in external_ids if external_id]
    parts = [part for part in parts if part]
    if not parts:
        return {}

    by_module = {}
    for module, name in parts:
        by_module.setdefault(module, set()).add(name)

    resolved = {}
    for module, names in by_module.items():
        records = execute(
            models,
            db,
            uid,
            api_key,
            "ir.model.data",
            "search_read",
            [[("module", "=", module), ("name", "in", sorted(names)), ("model", "=", model_name)]],
            {"fields": ["module", "name", "res_id"], "limit": len(names) + 10},
        )
        for record in records:
            resolved[f"{record['module']}.{record['name']}"] = record["res_id"]
    return resolved


def main():
    db, uid, api_key, models = connect()
    safe_rows = read_csv(SAFE_IMPORT)
    archive_rows = read_csv(ARCHIVE_IMPORT)

    product_total = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_count",
        [[]],
        {"context": {"active_test": False}},
    )
    active_total = execute(models, db, uid, api_key, "product.template", "search_count", [[("active", "=", True)]])
    inactive_total = execute(models, db, uid, api_key, "product.template", "search_count", [[("active", "=", False)]])
    category_total = execute(models, db, uid, api_key, "product.category", "search_count", [[]])
    supplierinfo_total = execute(models, db, uid, api_key, "product.supplierinfo", "search_count", [[]])

    imported_external_ids = [row["ID"] for row in safe_rows if row.get("ID")]
    imported_id_map = resolve_external_ids(
        models, db, uid, api_key, "product.template", imported_external_ids
    )
    imported_res_ids = list(imported_id_map.values())
    matched_ids = len(imported_res_ids)

    sample_external_ids = imported_external_ids
    sample_ids = [imported_id_map[external_id] for external_id in sample_external_ids if external_id in imported_id_map]
    sample_products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [sample_ids, ["id", "default_code", "name", "categ_id", "seller_ids", "standard_price", "list_price"]],
    )
    safe_by_res_id = {
        imported_id_map[row["ID"]]: row
        for row in safe_rows
        if row.get("ID") in imported_id_map
    }
    mismatches = []
    for product in sample_products:
        expected = safe_by_res_id.get(product["id"], {})
        category_name = product["categ_id"][1] if product.get("categ_id") else ""
        if product.get("name") != expected.get("Name") or category_name != expected.get("Product Category"):
            mismatches.append({
                "ID": product["id"],
                "Internal Reference": product.get("default_code", ""),
                "Expected Name": expected.get("Name", ""),
                "Odoo Name": product.get("name", ""),
                "Expected Category": expected.get("Product Category", ""),
                "Odoo Category": category_name,
            })

    supplier_infos = execute(
        models,
        db,
        uid,
        api_key,
        "product.supplierinfo",
        "search_read",
        [[("partner_id.name", "=", "Vendor TBD")]],
        {"fields": ["product_tmpl_id", "partner_id", "price", "delay", "min_qty"], "limit": 10000},
    )
    vendor_line_counts = Counter(
        row["product_tmpl_id"][0] for row in supplier_infos if row.get("product_tmpl_id")
    )
    duplicate_vendor_products = [
        {"Product Template ID": product_id, "Vendor TBD Lines": count}
        for product_id, count in vendor_line_counts.items()
        if count > 1
    ]

    archive_external_ids = [row["ID"] for row in archive_rows if row.get("ID")]
    archive_id_map = resolve_external_ids(
        models, db, uid, api_key, "product.template", archive_external_ids
    )
    archive_ids = list(archive_id_map.values())
    archived_matches = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_count",
        [[("id", "in", archive_ids), ("active", "=", False)]],
    )
    still_active_archive = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_count",
        [[("id", "in", archive_ids), ("active", "=", True)]],
    )

    category_names = execute(
        models,
        db,
        uid,
        api_key,
        "product.category",
        "search_read",
        [[]],
        {"fields": ["complete_name"], "limit": 10000},
    )
    category_counter = Counter(row.get("Product Category", "") for row in safe_rows)
    existing_category_names = {row.get("complete_name", "") for row in category_names}
    missing_categories = [
        {"Product Category": category, "Safe Import Rows": count}
        for category, count in sorted(category_counter.items())
        if category and category not in existing_category_names
    ]

    write_csv(
        REPORT_DIR / "odoo_live_sample_mismatches.csv",
        mismatches,
        ["ID", "Internal Reference", "Expected Name", "Odoo Name", "Expected Category", "Odoo Category"],
    )
    write_csv(
        REPORT_DIR / "odoo_live_duplicate_vendor_tbd_lines.csv",
        duplicate_vendor_products,
        ["Product Template ID", "Vendor TBD Lines"],
    )
    write_csv(
        REPORT_DIR / "odoo_live_missing_categories.csv",
        missing_categories,
        ["Product Category", "Safe Import Rows"],
    )

    print(f"Connected uid: {uid}")
    print(f"Products total: {product_total}")
    print(f"Products active: {active_total}")
    print(f"Products inactive: {inactive_total}")
    print(f"Product categories: {category_total}")
    print(f"Supplierinfo/vendor lines: {supplierinfo_total}")
    print(f"Safe import rows: {len(safe_rows)}")
    print(f"Safe import external IDs found in Odoo: {matched_ids}")
    print(f"Imported products checked: {len(sample_products)}")
    print(f"Name/category mismatches: {len(mismatches)}")
    print(f"Vendor TBD supplierinfo lines: {len(supplier_infos)}")
    print(f"Products with duplicate Vendor TBD lines: {len(duplicate_vendor_products)}")
    print(f"Archive import rows: {len(archive_rows)}")
    print(f"Archive rows already inactive: {archived_matches}")
    print(f"Archive rows still active: {still_active_archive}")
    print(f"Missing safe-import categories in Odoo: {len(missing_categories)}")


if __name__ == "__main__":
    main()
