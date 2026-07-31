"""Remove the final sellable products from the Miscellaneous category.

Assignments are explicit by live product ID. Names are normalized and retain
the OEM reference so website search and product cards remain useful. Default
mode is a dry run; use ``--apply`` to update Odoo.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from datetime import datetime
from pathlib import Path
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "outputs"


CATEGORY_IDS = {
    "Parts / Driveline": {
        6692, 6334, 6526, 6745, 6480, 10664, 10616, 6470, 6478, 6650,
        11065, 10847, 6667, 10624, 6528, 11047, 10580, 6654, 6611, 6462,
        6507, 6516, 11069,
    },
    "Parts / Hardware": {
        6324, 6328, 10914, 6386, 6660, 10690, 10674, 10584, 6666, 6423,
        6425, 6426, 11104, 6612, 6544, 6504, 6404,
    },
    "Parts / Seals / Oil Seals": {10749, 11043, 10719, 6637, 10813},
    "Parts / Seals / Hydraulic Seals": {6448, 11080, 10895},
    "Parts / Implements": {10587, 6750, 6749, 10685, 10906},
    "Parts / Ground Engaging Tools": {6327},
    "Parts / Seals / Hydraulic Seal Kits": {6332, 10736},
    "Parts / Cab": {
        6520, 3701, 6489, 6521, 6372, 11114, 6763, 6793, 10696, 6369,
        10697, 10626, 10944, 10943, 10591, 10590,
    },
    "Parts / Fuel System": {6570, 6546, 6545, 10922, 10709},
    "Parts / Hydraulic / Hydraulic Hoses": {6715},
    "Parts / Linkage": {6438},
    "Parts / Electrical": {
        6743, 10569, 6403, 11180, 10543, 6608, 10785, 3736,
    },
    "Parts / Filters": {6486},
    "Parts / Hydraulic": {6635, 10923, 10657, 10629},
    "Parts / Shop Supplies": {12601},
    "Parts / Engine": {6688, 6689},
}


SPECIAL_NAMES = {
    6324: "Hardware - 1/2 x 1-1/2 in",
    6328: "Hardware - 9/16 x 2-1/2 in",
    10749: "Oil Seal - 25 x 40 x 7 mm",
    10914: "Hardware Component - A-HP109",
    10629: "Cartridge Assembly",
    6507: "Collar",
    10709: "Lift Pump",
    10696: "Inner Grille Light",
    10697: "Light Grille",
    10719: "Seal Assembly",
    11080: "Hydraulic Rod Seal",
    6637: "Heat Seal",
    10584: "Positioning Component",
    10895: "Hydraulic Wear Component",
}


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(
        db, username, api_key, {}
    )
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def public_path_id(models, db, uid, api_key, website_id: int, path: str) -> int:
    parent_id = None
    for name in [part.strip() for part in path.split("/") if part.strip()]:
        ids = execute(
            models,
            db,
            uid,
            api_key,
            "product.public.category",
            "search",
            [
                [
                    ("name", "=", name),
                    ("parent_id", "=", parent_id or False),
                    "|",
                    ("website_id", "=", website_id),
                    ("website_id", "=", False),
                ]
            ],
            {"limit": 1},
        )
        if not ids:
            raise RuntimeError(f"Missing website category path: {path}")
        parent_id = ids[0]
    return parent_id


def normalized_name(product: dict) -> str:
    base = SPECIAL_NAMES.get(product["id"], product.get("name") or "Part")
    base = re.sub(r"\bColalr\b", "Collar", base, flags=re.IGNORECASE)
    base = re.sub(r"\bCartr Assy\b", "Cartridge Assembly", base, flags=re.IGNORECASE)
    base = re.sub(r"\bList Pump\b", "Lift Pump", base, flags=re.IGNORECASE)
    base = re.sub(r"\bSeal,heat\b", "Heat Seal", base, flags=re.IGNORECASE)
    oem = " / ".join(
        part.strip()
        for part in re.split(r"[,;|]+", product.get("x_studio_oem_part_number") or "")
        if part.strip()
    )
    if oem and not all(
        re.sub(r"[^A-Z0-9]", "", part.upper())
        in re.sub(r"[^A-Z0-9]", "", base.upper())
        for part in oem.split(" / ")
    ):
        base = f"{base} - OEM {oem}"
    return base


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    id_to_category = {
        product_id: category
        for category, product_ids in CATEGORY_IDS.items()
        for product_id in product_ids
    }
    if len(id_to_category) != sum(len(ids) for ids in CATEGORY_IDS.values()):
        raise RuntimeError("A product ID appears in more than one category assignment")

    db, uid, api_key, models = connect()
    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [sorted(id_to_category)],
        {
            "fields": [
                "id",
                "default_code",
                "name",
                "categ_id",
                "public_categ_ids",
                "x_studio_oem_part_number",
            ]
        },
    )
    live_ids = {product["id"] for product in products}
    if live_ids != set(id_to_category):
        raise RuntimeError(
            f"Product ID guard failed; missing={sorted(set(id_to_category) - live_ids)}"
        )

    internal_rows = execute(
        models,
        db,
        uid,
        api_key,
        "product.category",
        "search_read",
        [[("complete_name", "in", sorted(CATEGORY_IDS))]],
        {"fields": ["id", "complete_name"], "limit": 100},
    )
    internal_by_path = {row["complete_name"]: row["id"] for row in internal_rows}
    missing_internal = sorted(set(CATEGORY_IDS) - set(internal_by_path))
    if missing_internal:
        raise RuntimeError(f"Missing internal categories: {missing_internal}")

    websites = execute(
        models,
        db,
        uid,
        api_key,
        "website",
        "search_read",
        [[]],
        {"fields": ["id", "name"], "limit": 100},
    )
    website = next(
        (row for row in websites if "southern" in row["name"].lower()), websites[0]
    )
    public_by_path = {
        path: public_path_id(models, db, uid, api_key, website["id"], path)
        for path in CATEGORY_IDS
    }

    rows = []
    for product in products:
        target = id_to_category[product["id"]]
        new_name = normalized_name(product)
        values = {
            "name": new_name,
            "categ_id": internal_by_path[target],
            "public_categ_ids": [(6, 0, [public_by_path[target]])],
        }
        if args.apply:
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "write",
                [[product["id"]], values],
            )
        rows.append(
            {
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code") or "",
                "Old Name": product.get("name") or "",
                "New Name": new_name,
                "Old Category": (
                    product["categ_id"][1] if product.get("categ_id") else ""
                ),
                "New Category": target,
                "Status": "Updated" if args.apply else "Would update",
            }
        )

    OUT_DIR.mkdir(exist_ok=True)
    report = OUT_DIR / (
        "final_misc_catalog_update_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(rows),
            "updated": len(rows) if args.apply else 0,
            "report": str(report),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
