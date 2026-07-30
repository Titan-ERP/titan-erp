from __future__ import annotations

import csv
import os
import re
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
INPUT = ROOT / "odoo_imports/product_master/review_reports/odoo_archive_candidates_refreshed.csv"
RESULTS = ROOT / "odoo_imports/product_master/review_reports/odoo_archive_candidates_apply_results.csv"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def main() -> None:
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    with INPUT.open(newline="", encoding="utf-8-sig") as f:
        rows = [row for row in csv.DictReader(f) if row.get("Decision") == "Archive"]

    results = []
    for row in rows:
        result = {
            "Timestamp": datetime.now().isoformat(timespec="seconds"),
            "Product ID": row["Product ID"],
            "Internal Reference": row["Internal Reference"],
            "Name": row["Name"],
            "Status": "Applied",
            "Verified Inactive": "No",
            "Error": "",
        }
        try:
            product_id = int(row["Product ID"])
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"active": False}])
            after = execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [[product_id]],
                {"fields": ["active"], "context": {"active_test": False}},
            )[0]
            result["Verified Inactive"] = "Yes" if after.get("active") is False else "No"
        except Exception as exc:  # noqa: BLE001 - keep going and log row-level failures.
            result["Status"] = "Failed"
            result["Error"] = re.sub(r"\s+", " ", str(exc))[:1000]
        results.append(result)

    fields = ["Timestamp", "Product ID", "Internal Reference", "Name", "Status", "Verified Inactive", "Error"]
    with RESULTS.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    failed = [row for row in results if row["Status"] == "Failed" or row["Verified Inactive"] != "Yes"]
    print(f"Rows attempted: {len(results)}")
    print(f"Verified inactive: {len(results) - len(failed)}/{len(results)}")
    print(f"Results: {RESULTS}")
    if failed:
        raise SystemExit("Some archive actions failed verification.")


if __name__ == "__main__":
    main()
