import csv
import os
import re
import xmlrpc.client
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
SAFE_IMPORT = PRODUCT_MASTER / "import_ready" / "odoo_exact_schema_safe_import.csv"
ARCHIVE_IMPORT = PRODUCT_MASTER / "import_ready" / "odoo_archive_products_set_inactive.csv"
REPORT_DIR = PRODUCT_MASTER / "review_reports"
AUDIT = REPORT_DIR / "odoo_product_live_inefficiency_audit.csv"
PROTECTED_ARCHIVE = REPORT_DIR / "odoo_archive_candidates_protected_after_cleanup.csv"

GENERIC_NAMES = {
    "bearing",
    "seal",
    "filter",
    "adapter",
    "switch",
    "pump",
    "misc",
    "part",
    "oem part",
    "repair kit",
}


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


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
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "ir.model.data",
            "search_read",
            [[("module", "=", module), ("name", "in", sorted(names)), ("model", "=", model_name)]],
            {"fields": ["module", "name", "res_id"], "limit": len(names) + 20},
        )
        for row in rows:
            resolved[f"{row['module']}.{row['name']}"] = row["res_id"]
    return resolved


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def read_all_products(models, db, uid, api_key, fields):
    ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [[]],
        {"context": {"active_test": False}, "order": "default_code asc,id asc"},
    )
    products = []
    for index in range(0, len(ids), 500):
        products.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [ids[index : index + 500]],
                {"fields": fields, "context": {"active_test": False}},
            )
        )
    return products


def main():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    fields = ["id", "default_code", "name", "active", "categ_id", "seller_ids", "sale_ok", "purchase_ok"]
    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["type"]})
    for optional in ["is_storable", "type", "detailed_type", "standard_price", "list_price"]:
        if optional in product_fields:
            fields.append(optional)

    products = read_all_products(models, db, uid, api_key, fields)

    default_counts = Counter(
        (row.get("default_code") or "").strip().upper()
        for row in products
        if row.get("active") and (row.get("default_code") or "").strip()
    )
    rows = []
    for product in products:
        if not product.get("active"):
            continue
        code = (product.get("default_code") or "").strip()
        clean_name = re.sub(r"\s+", " ", (product.get("name") or "").strip())
        lower_name = clean_name.lower()
        product_type = product.get("detailed_type") or product.get("type") or ""
        is_storable = product.get("is_storable", "")
        if not code:
            rows.append({"Issue": "Missing Internal Reference", "Product ID": product["id"], "Internal Reference": code, "Name": clean_name, "Category": rel_name(product.get("categ_id")), "Recommended Action": "Review before update"})
        elif default_counts[code.upper()] > 1:
            rows.append({"Issue": "Duplicate Internal Reference", "Product ID": product["id"], "Internal Reference": code, "Name": clean_name, "Category": rel_name(product.get("categ_id")), "Recommended Action": "Review duplicates"})
        if lower_name in GENERIC_NAMES:
            rows.append({"Issue": "Generic Product Name", "Product ID": product["id"], "Internal Reference": code, "Name": clean_name, "Category": rel_name(product.get("categ_id")), "Recommended Action": "Needs naming research/review"})
        if code and clean_name.upper().startswith(code.upper() + " "):
            rows.append({"Issue": "Name Starts With Internal Reference", "Product ID": product["id"], "Internal Reference": code, "Name": clean_name, "Category": rel_name(product.get("categ_id")), "Recommended Action": "Remove duplicate part code from name"})
        category = rel_name(product.get("categ_id"))
        vendor_line_expected = (
            product.get("active")
            and product_type in {"product", "consu"}
            and category.startswith(("Parts", "Consumable"))
            and (bool(is_storable) or category.startswith("Parts"))
            and not product.get("seller_ids")
        )
        if vendor_line_expected:
            rows.append({"Issue": "No Vendor Line", "Product ID": product["id"], "Internal Reference": code, "Name": clean_name, "Category": rel_name(product.get("categ_id")), "Recommended Action": "Add Vendor TBD if this is an inventory/parts product"})

    archive_rows = read_csv(ARCHIVE_IMPORT)
    protected_archive_ids = {
        int(row["Product ID"])
        for row in read_csv(PROTECTED_ARCHIVE)
        if row.get("Product ID") and row.get("Decision") == "Protected"
    }
    archive_ids = [row.get("ID", "") for row in archive_rows if row.get("ID")]
    resolved_archive = resolve_external_ids(models, db, uid, api_key, "product.template", archive_ids)
    if resolved_archive:
        active_archive = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "search_read",
            [[("id", "in", list(resolved_archive.values())), ("active", "=", True)]],
            {"fields": ["id", "default_code", "name", "categ_id"], "limit": 10000},
        )
        for product in active_archive:
            if product["id"] in protected_archive_ids:
                continue
            rows.append({"Issue": "Archive Candidate Still Active", "Product ID": product["id"], "Internal Reference": product.get("default_code", ""), "Name": product.get("name", ""), "Category": rel_name(product.get("categ_id")), "Recommended Action": "Set active False if not system-protected"})

    write_csv(AUDIT, rows, ["Issue", "Product ID", "Internal Reference", "Name", "Category", "Recommended Action"])

    counts = Counter(row["Issue"] for row in rows)
    print(f"Connected uid: {uid}")
    print(f"Products audited: {len(products)}")
    for issue, count in sorted(counts.items()):
        print(f"{issue}: {count}")
    print(f"Audit: {AUDIT}")


if __name__ == "__main__":
    main()
