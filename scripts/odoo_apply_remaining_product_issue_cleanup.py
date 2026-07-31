from __future__ import annotations

import csv
import os
import re
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
RESULTS = ROOT / "odoo_imports/product_master/review_reports/odoo_remaining_product_issue_cleanup_results.csv"


UPDATES = [
    {
        "id": 11266,
        "default_code": "S.39",
        "name": "Linch Pin - Sparex S.39 - 7/16 x 1-3/4 in",
        "category": "Parts / Hardware",
        "manufacturer": "Sparex",
        "source": "https://www.amazon.com/Linch-Pin-Ring-Inch-Pack/dp/B01KBPVSGI",
        "notes": "Sparex S.39 is listed as a linch pin with ring, 7/16 x 1-3/4 in.",
    },
    {
        "id": 11208,
        "default_code": "50-2408",
        "name": "Worm Gear Hose Clamp #8 - 7/16 to 15/16 in",
        "category": "Parts / Hardware",
        "manufacturer": "Kimball Midwest",
        "source": "https://www.kimballmidwest.com/502408",
        "notes": "Kimball Midwest item 502408 is a #8 stainless hose clamp with 7/16 to 15/16 in clamp range.",
    },
    {
        "id": 11204,
        "default_code": "KFM-BC129-PL",
        "name": "Reversible Planer Knife - Bobcat Forestry Mulcher - KFM-BC129-PL",
        "category": "Parts / Ground Engaging Tools",
        "manufacturer": "",
        "source": "https://www.xtremewearparts.com/xtreme-XFM-BC129-PL-reversible-planer-knife-fits-bobcat-frc50-mulcher-teeth-serial-atsy00000-and-up-quadco-part-q11318t",
        "notes": "Likely equivalent to XFM-BC129-PL reversible planer knife; retained local KFM code to avoid overwriting uncertain internal reference.",
    },
    {
        "id": 6984,
        "default_code": "HAULING-FEE",
        "name": "Hauling Fee",
        "category": "Freight",
        "manufacturer": "",
        "source": "",
        "notes": "Fee product; no vendor line needed.",
    },
    {
        "id": 7019,
        "default_code": "SHOP-FEE",
        "name": "Shop Fee",
        "category": "Field Service",
        "manufacturer": "",
        "source": "",
        "notes": "Fee product; no vendor line needed.",
    },
    {
        "id": 11216,
        "default_code": "AUGER-PREDATOR",
        "name": "Auger - Predator Gas-Powered",
        "category": "Equipment",
        "manufacturer": "Predator",
        "source": "",
        "notes": "Equipment/rental-style product; no vendor line added in this pass.",
    },
    {
        "id": 7042,
        "default_code": "TX10",
        "name": "TX10",
        "category": "Equipment",
        "manufacturer": "",
        "source": "",
        "notes": "Equipment/model-style product; assigned internal reference from product name.",
    },
    {
        "id": 7044,
        "default_code": "U35",
        "name": "U35",
        "category": "Equipment / Mini-Excavator",
        "manufacturer": "Kubota",
        "source": "",
        "notes": "Equipment/model-style product; assigned internal reference from product name.",
    },
    {
        "id": 11271,
        "default_code": "FRT",
        "name": "FRT",
        "category": "Freight",
        "manufacturer": "",
        "source": "",
        "notes": "Likely freight shorthand; categorized as Freight and assigned internal reference from product name.",
    },
]


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
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    categories = execute(
        models,
        db,
        uid,
        api_key,
        "product.category",
        "search_read",
        [[("complete_name", "in", sorted({row["category"] for row in UPDATES}))]],
        {"fields": ["id", "complete_name"], "limit": len(UPDATES) + 20},
    )
    category_by_name = {row["complete_name"]: row["id"] for row in categories}

    results = []
    for update in UPDATES:
        result = {
            "Timestamp": datetime.now().isoformat(timespec="seconds"),
            "Product ID": update["id"],
            "Internal Reference": update["default_code"],
            "New Name": update["name"],
            "Category": update["category"],
            "Manufacturer": update["manufacturer"],
            "Status": "Applied",
            "Verified": "No",
            "Source": update["source"],
            "Notes": update["notes"],
            "Error": "",
        }
        try:
            category_id = category_by_name[update["category"]]
            values = {
                "default_code": update["default_code"],
                "name": update["name"],
                "categ_id": category_id,
                "x_studio_manufacturer": update["manufacturer"],
            }
            execute(models, db, uid, api_key, "product.template", "write", [[update["id"]], values])
            after = execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [[update["id"]]],
                {"fields": ["default_code", "name", "categ_id", "x_studio_manufacturer"], "context": {"active_test": False}},
            )[0]
            result["Verified"] = (
                "Yes"
                if after.get("default_code") == update["default_code"]
                and after.get("name") == update["name"]
                and rel_name(after.get("categ_id")) == update["category"]
                and (after.get("x_studio_manufacturer") or "") == update["manufacturer"]
                else "No"
            )
        except Exception as exc:  # noqa: BLE001
            result["Status"] = "Failed"
            result["Error"] = re.sub(r"\s+", " ", str(exc))[:1000]
        results.append(result)

    fields = ["Timestamp", "Product ID", "Internal Reference", "New Name", "Category", "Manufacturer", "Status", "Verified", "Source", "Notes", "Error"]
    with RESULTS.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    failed = [row for row in results if row["Status"] != "Applied" or row["Verified"] != "Yes"]
    print(f"Rows attempted: {len(results)}")
    print(f"Verified: {len(results) - len(failed)}/{len(results)}")
    print(f"Results: {RESULTS}")
    if failed:
        raise SystemExit("Some cleanup actions failed verification.")


if __name__ == "__main__":
    main()
