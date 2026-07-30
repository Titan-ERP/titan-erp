from __future__ import annotations

import csv
import os
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PROBE = ROOT / "odoo_imports/product_master/review_reports/web_product_cleanup_live_probe.csv"
RESULTS = ROOT / "odoo_imports/product_master/review_reports/web_product_cleanup_import_results.csv"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def main() -> None:
    dry_run = "--apply" not in sys.argv
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    with PROBE.open(newline="", encoding="utf-8-sig") as f:
        rows = [row for row in csv.DictReader(f) if row["Ready For Direct Update"] == "Yes"]

    results = []
    for row in rows:
        product_id = int(row["Product ID"])
        category_id = int(row["Proposed Category ID"])
        before = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [[product_id]],
            {"fields": ["id", "default_code", "name", "categ_id", "x_studio_manufacturer", "active"]},
        )[0]
        values = {
            "name": row["Proposed Name"],
            "categ_id": category_id,
            "x_studio_manufacturer": row["Manufacturer"],
        }
        status = "DRY RUN"
        if not dry_run:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], values])
            status = "UPDATED"
        after = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [[product_id]],
            {"fields": ["id", "default_code", "name", "categ_id", "x_studio_manufacturer", "active"]},
        )[0]
        passed = (
            after.get("name") == values["name"]
            and rel_name(after.get("categ_id")) == row["Proposed Category"]
            and (after.get("x_studio_manufacturer") or "") == values["x_studio_manufacturer"]
        )
        results.append(
            {
                "Timestamp": datetime.now().isoformat(timespec="seconds"),
                "Status": status,
                "Verify Passed": "Yes" if dry_run or passed else "No",
                "Product ID": product_id,
                "Internal Reference": before.get("default_code", ""),
                "Before Name": before.get("name", ""),
                "After Name": after.get("name", ""),
                "Before Category": rel_name(before.get("categ_id")),
                "After Category": rel_name(after.get("categ_id")),
                "Before Manufacturer": before.get("x_studio_manufacturer") or "",
                "After Manufacturer": after.get("x_studio_manufacturer") or "",
                "Source URL": row["Source URL"],
            }
        )

    fields = [
        "Timestamp",
        "Status",
        "Verify Passed",
        "Product ID",
        "Internal Reference",
        "Before Name",
        "After Name",
        "Before Category",
        "After Category",
        "Before Manufacturer",
        "After Manufacturer",
        "Source URL",
    ]
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    print(f"Mode: {'APPLY' if not dry_run else 'DRY RUN'}")
    print(f"Rows: {len(results)}")
    print(f"Results: {RESULTS}")
    if not dry_run:
        failed = [row for row in results if row["Verify Passed"] != "Yes"]
        print(f"Verified: {len(results) - len(failed)}/{len(results)}")
        if failed:
            raise SystemExit("Some updates failed verification.")


if __name__ == "__main__":
    main()
