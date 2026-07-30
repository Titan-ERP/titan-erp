import csv
import os
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SAFE_IMPORT = ROOT / "odoo_imports" / "product_master" / "import_ready" / "odoo_exact_schema_safe_import.csv"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"
PLAN_PATH = REPORT_DIR / "odoo_vendor_duplicate_cleanup_plan.csv"


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


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
    by_module = defaultdict(set)
    for module, name in parts:
        by_module[module].add(name)

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


def as_decimal(value):
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.0001"))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def score_line(line, expected):
    score = 0
    if as_decimal(line.get("price")) == as_decimal(expected.get("Vendors/price")):
        score += 4
    if as_decimal(line.get("min_qty")) == as_decimal(expected.get("Vendors/min_qty")):
        score += 2
    if str(line.get("delay", "") or "") == str(expected.get("Vendors/delay", "") or ""):
        score += 2
    if (line.get("product_code") or "") == (expected.get("Vendors Product Code") or ""):
        score += 1
    return score


def main():
    db, uid, api_key, models = connect()
    safe_rows = read_csv(SAFE_IMPORT)
    external_ids = [row["ID"] for row in safe_rows if row.get("ID")]
    id_map = resolve_external_ids(models, db, uid, api_key, "product.template", external_ids)
    expected_by_product_id = {
        id_map[row["ID"]]: row
        for row in safe_rows
        if row.get("ID") in id_map
    }

    supplier_infos = execute(
        models,
        db,
        uid,
        api_key,
        "product.supplierinfo",
        "search_read",
        [[("partner_id.name", "=", "Vendor TBD")]],
        {
            "fields": [
                "id",
                "product_tmpl_id",
                "partner_id",
                "price",
                "delay",
                "min_qty",
                "product_code",
                "product_name",
                "sequence",
                "create_date",
                "write_date",
            ],
            "limit": 10000,
        },
    )

    grouped = defaultdict(list)
    for line in supplier_infos:
        product = line.get("product_tmpl_id")
        if product:
            grouped[product[0]].append(line)

    plan_rows = []
    delete_ids = []
    for product_id, lines in sorted(grouped.items()):
        if len(lines) < 2:
            continue
        expected = expected_by_product_id.get(product_id, {})
        ranked = sorted(
            lines,
            key=lambda line: (-score_line(line, expected), line.get("id", 0)),
        )
        keep = ranked[0]
        for line in ranked[1:]:
            delete_ids.append(line["id"])
            plan_rows.append(
                {
                    "Product Template ID": product_id,
                    "Product": line["product_tmpl_id"][1],
                    "Keep Supplierinfo ID": keep["id"],
                    "Delete Supplierinfo ID": line["id"],
                    "Vendor": line["partner_id"][1],
                    "Deleted Price": line.get("price", ""),
                    "Deleted Min Qty": line.get("min_qty", ""),
                    "Deleted Delay": line.get("delay", ""),
                    "Expected Price": expected.get("Vendors/price", ""),
                    "Expected Min Qty": expected.get("Vendors/min_qty", ""),
                    "Expected Delay": expected.get("Vendors/delay", ""),
                }
            )

    write_csv(
        PLAN_PATH,
        plan_rows,
        [
            "Product Template ID",
            "Product",
            "Keep Supplierinfo ID",
            "Delete Supplierinfo ID",
            "Vendor",
            "Deleted Price",
            "Deleted Min Qty",
            "Deleted Delay",
            "Expected Price",
            "Expected Min Qty",
            "Expected Delay",
        ],
    )

    if delete_ids:
        execute(models, db, uid, api_key, "product.supplierinfo", "unlink", [delete_ids])

    remaining = execute(
        models,
        db,
        uid,
        api_key,
        "product.supplierinfo",
        "search_read",
        [[("partner_id.name", "=", "Vendor TBD")]],
        {"fields": ["product_tmpl_id"], "limit": 10000},
    )
    remaining_counts = defaultdict(int)
    for line in remaining:
        product = line.get("product_tmpl_id")
        if product:
            remaining_counts[product[0]] += 1

    duplicate_products_remaining = sum(1 for count in remaining_counts.values() if count > 1)
    print(f"Duplicate products found before cleanup: {len(plan_rows)}")
    print(f"Supplierinfo lines deleted: {len(delete_ids)}")
    print(f"Products with duplicate Vendor TBD lines remaining: {duplicate_products_remaining}")
    print(f"Cleanup plan written: {PLAN_PATH}")


if __name__ == "__main__":
    main()
