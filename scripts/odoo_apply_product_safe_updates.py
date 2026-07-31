import csv
import os
import re
import sys
import xmlrpc.client
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
SAFE_IMPORT = PRODUCT_MASTER / "import_ready" / "odoo_exact_schema_safe_import.csv"
ARCHIVE_IMPORT = PRODUCT_MASTER / "import_ready" / "odoo_archive_products_set_inactive.csv"
REPORT_DIR = PRODUCT_MASTER / "review_reports"
PLAN = REPORT_DIR / "odoo_product_safe_update_plan.csv"
RESULTS = REPORT_DIR / "odoo_product_safe_update_results.csv"

TARGET_VENDOR_NAME = "Vendor TBD"
VENDOR_TARGET_CATEGORIES = ("Parts", "Consumable")


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read(models, db, uid, api_key, model, domain, fields, limit=10000, order=None, context=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    if context:
        kwargs["context"] = context
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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
    return (module, name) if module and name else None


def resolve_external_ids(models, db, uid, api_key, model_name, external_ids):
    by_module = defaultdict(set)
    for external_id in external_ids:
        part = split_external_id(external_id)
        if part:
            by_module[part[0]].add(part[1])
    resolved = {}
    for module, names in by_module.items():
        rows = read(
            models,
            db,
            uid,
            api_key,
            "ir.model.data",
            [("module", "=", module), ("name", "in", sorted(names)), ("model", "=", model_name)],
            ["module", "name", "res_id"],
            limit=len(names) + 20,
        )
        for row in rows:
            resolved[f"{row['module']}.{row['name']}"] = row["res_id"]
    return resolved


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def money(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def safe_name_mismatch_updates(models, db, uid, api_key):
    safe_rows = read_csv(SAFE_IMPORT)
    external_ids = [row["ID"] for row in safe_rows if row.get("ID")]
    id_map = resolve_external_ids(models, db, uid, api_key, "product.template", external_ids)
    expected_by_id = {id_map[row["ID"]]: row for row in safe_rows if row.get("ID") in id_map}
    products = read(
        models,
        db,
        uid,
        api_key,
        "product.template",
        [("id", "in", list(expected_by_id.keys()))],
        ["id", "default_code", "name", "categ_id"],
        context={"active_test": False},
    )
    actions = []
    for product in products:
        expected = expected_by_id[product["id"]]
        expected_name = expected.get("Name", "").strip()
        if expected_name and product.get("name") != expected_name and rel_name(product.get("categ_id")) == expected.get("Product Category"):
            actions.append(
                {
                    "Action": "Update Name",
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code", ""),
                    "Current Name": product.get("name", ""),
                    "New Name": expected_name,
                    "Reason": "Safe import master name differs and category matches.",
                }
            )
    return actions


def archive_actions(models, db, uid, api_key):
    archive_rows = read_csv(ARCHIVE_IMPORT)
    external_ids = [row["ID"] for row in archive_rows if row.get("ID")]
    id_map = resolve_external_ids(models, db, uid, api_key, "product.template", external_ids)
    products = read(
        models,
        db,
        uid,
        api_key,
        "product.template",
        [("id", "in", list(id_map.values())), ("active", "=", True)],
        ["id", "default_code", "name", "categ_id"],
        limit=10000,
    )
    return [
        {
            "Action": "Archive Product",
            "Product ID": product["id"],
            "Internal Reference": product.get("default_code", ""),
            "Current Name": product.get("name", ""),
            "New Name": "",
            "Reason": "Active product appears in non-protected archive candidate import.",
        }
        for product in products
    ]


def vendor_actions(models, db, uid, api_key):
    vendor_rows = read(models, db, uid, api_key, "res.partner", [("name", "=", TARGET_VENDOR_NAME)], ["id", "name"], limit=2)
    if len(vendor_rows) != 1:
        raise SystemExit(f"Expected exactly one {TARGET_VENDOR_NAME}; found {len(vendor_rows)}")
    vendor_id = vendor_rows[0]["id"]

    fields = ["id", "default_code", "name", "active", "categ_id", "seller_ids", "standard_price"]
    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    for optional in ["type", "detailed_type", "is_storable"]:
        if optional in product_fields:
            fields.append(optional)

    products = read(
        models,
        db,
        uid,
        api_key,
        "product.template",
        [("active", "=", True)],
        fields,
        limit=10000,
        order="default_code asc,id asc",
    )
    actions = []
    for product in products:
        category = rel_name(product.get("categ_id"))
        if not category.startswith(VENDOR_TARGET_CATEGORIES):
            continue
        if product.get("seller_ids"):
            continue
        if not (product.get("default_code") or "").strip():
            continue
        product_type = product.get("detailed_type") or product.get("type") or ""
        if product_type not in {"product", "consu", ""}:
            continue
        actions.append(
            {
                "Action": "Add Vendor TBD",
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code", ""),
                "Current Name": product.get("name", ""),
                "New Name": "",
                "Reason": f"Active {category} product has no vendor/seller line.",
                "Vendor ID": vendor_id,
                "Vendor Price": money(product.get("standard_price")),
            }
        )
    return actions


def main():
    apply = "--apply" in sys.argv
    skip_archive = "--skip-archive" in sys.argv
    skip_vendor = "--skip-vendor" in sys.argv
    skip_names = "--skip-names" in sys.argv
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    name_actions = [] if skip_names else safe_name_mismatch_updates(models, db, uid, api_key)
    archive_plan = [] if skip_archive else archive_actions(models, db, uid, api_key)
    vendor_plan = [] if skip_vendor else vendor_actions(models, db, uid, api_key)
    all_actions = name_actions + archive_plan + vendor_plan

    fields = ["Action", "Product ID", "Internal Reference", "Current Name", "New Name", "Reason", "Vendor ID", "Vendor Price", "Status", "Error"]
    write_csv(PLAN, all_actions, fields)

    results = []
    if apply:
        for action in all_actions:
            result = dict(action)
            result["Status"] = "Applied"
            result["Error"] = ""
            try:
                product_id = int(action["Product ID"])
                if action["Action"] == "Update Name":
                    execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"name": action["New Name"]}])
                elif action["Action"] == "Archive Product":
                    execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"active": False}])
                elif action["Action"] == "Add Vendor TBD":
                    execute(
                        models,
                        db,
                        uid,
                        api_key,
                        "product.supplierinfo",
                        "create",
                        [{"partner_id": int(action["Vendor ID"]), "product_tmpl_id": product_id, "price": money(action["Vendor Price"]), "delay": 1, "min_qty": 1}],
                    )
            except Exception as exc:  # noqa: BLE001 - capture Odoo user errors per-row.
                result["Status"] = "Failed"
                result["Error"] = re.sub(r"\s+", " ", str(exc))[:1000]
            results.append(result)
    else:
        results = [dict(action, Status="Planned", Error="") for action in all_actions]
    write_csv(RESULTS, results, fields)

    print(f"Connected uid: {uid}")
    print(f"Apply: {apply}")
    print(f"Skip names: {skip_names}")
    print(f"Skip archive: {skip_archive}")
    print(f"Skip vendor: {skip_vendor}")
    print(f"Name updates: {len(name_actions)}")
    print(f"Archive actions: {len(archive_plan)}")
    print(f"Vendor TBD additions: {len(vendor_plan)}")
    print(f"Total actions: {len(all_actions)}")
    if apply:
        failed = sum(1 for row in results if row["Status"] == "Failed")
        print(f"Failed: {failed}")
    print(f"Plan: {PLAN}")
    print(f"Results: {RESULTS}")


if __name__ == "__main__":
    main()
