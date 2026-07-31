"""Apply exact-source product naming and category corrections.

Every entry is tied to an exact live product ID and OEM part number. Default
mode is a dry run; use ``--apply`` to update the product name, manufacturer,
internal category, and public website category.
"""

from __future__ import annotations

import argparse
import csv
import os
from datetime import datetime
from pathlib import Path
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "outputs"


OVERRIDES = {
    6571: {
        "oem": "TA040-93230",
        "name": "Outer Air Filter - Kubota TA040-93230",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "Kubota",
        "source": "Live Odoo exact OEM cross-reference to categorized Sparex S.76416",
    },
    10717: {
        "oem": "3A111-19130",
        "name": "Inner Air Filter - Kubota 3A111-19130",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "Kubota",
        "source": "Live Odoo exact OEM cross-reference to categorized Sparex S.76725",
    },
    10788: {
        "oem": "55231-26150",
        "name": "Inner Air Filter - 55231-26150",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "",
        "source": "Live Odoo exact OEM cross-reference to categorized Sparex S.154092",
    },
    6487: {
        "oem": "569-43-83920",
        "name": "Hydraulic Filter Element - Komatsu 569-43-83920",
        "category": "Parts / Filters / Hydraulic Filters",
        "manufacturer": "Komatsu",
        "source": "https://www.komatsu.com/en-us/products/parts/filters/569-43-83920",
    },
    6567: {
        "oem": "1G994-11210",
        "name": "Inner Air Filter - Kubota 1G994-11210",
        "category": "Parts / Filters / Air Filters",
        "manufacturer": "Kubota",
        "source": "https://www.messicks.com/parts/kubota/1G994-11210",
    },
    11173: {
        "oem": "V0631-51880",
        "name": "Fuel/Water Separator Element - Kubota V0631-51880",
        "category": "Parts / Filters / Fuel Water Separators",
        "manufacturer": "Kubota",
        "source": "https://www.messicks.com/parts/kubota/v0631-51880",
    },
    10558: {
        "oem": "119802-55710",
        "name": "Fuel Filter Element - Yanmar 119802-55710",
        "category": "Parts / Filters / Fuel Filters",
        "manufacturer": "Yanmar",
        "source": "https://www.buyyanmar.com/4TNV98T-NSA2_sub0001_ENGINE_017-1",
    },
    6674: {
        "oem": "600-331-2900",
        "name": "KCCV Filter Kit - Komatsu 600-331-2900",
        "category": "Parts / Filters",
        "manufacturer": "Komatsu",
        "source": "https://www.komatsu.com/en-us/products/parts/filters/600-331-2900",
    },
    11024: {
        "oem": "BF7922",
        "name": "Fuel Filter - Baldwin BF7922",
        "category": "Parts / Filters / Fuel Filters",
        "manufacturer": "Baldwin",
        "source": "https://www.grainger.com/product/BALDWIN-FILTERS-Fuel-Filter-Spin-On-4CUC6",
    },
    6402: {
        "oem": "42128",
        "name": "Double Fuel Filter Head - Sparex S.42128",
        "category": "Parts / Fuel System",
        "manufacturer": "Sparex",
        "source": "https://ca.sparex.com/double-filter-head-42128.html",
    },
    6712: {
        "oem": "42127",
        "name": "Single Fuel Filter Head - Sparex S.42127",
        "category": "Parts / Fuel System",
        "manufacturer": "Sparex",
        "source": "https://masseytractorparts.com/en-es/products/filter-head-s-42127",
    },
    10957: {
        "oem": "921-3018A",
        "name": "Single-Lip Oil Seal - MTD 921-3018A - 1.25 x 1.874 x 0.25 in",
        "category": "Parts / Seals / Oil Seals",
        "manufacturer": "MTD",
        "source": "https://www.messicks.com/parts/cub-cadet/921-3018a",
    },
    10649: {
        "oem": "1R0751",
        "name": "Advanced Efficiency Fuel Filter - Caterpillar 1R-0751",
        "category": "Parts / Filters / Fuel Filters",
        "manufacturer": "Caterpillar",
        "source": "https://parts.cat.com/en/catcorp/1R-0751",
    },
    10967: {
        "oem": "9R2499",
        "name": "Lip-Type Oil Seal - Caterpillar 9R-2499",
        "category": "Parts / Seals / Oil Seals",
        "manufacturer": "Caterpillar",
        "source": "https://gtengineparts.com/caterpillar/9r2499",
    },
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
    return (
        db,
        uid,
        api_key,
        xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object"),
    )


def ensure_public_path(
    models, db, uid, api_key, website_id: int, complete_name: str
) -> int:
    parent_id = None
    for name in [part.strip() for part in complete_name.split("/") if part.strip()]:
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
        if ids:
            parent_id = ids[0]
            continue
        values = {"name": name, "website_id": website_id}
        if parent_id:
            values["parent_id"] = parent_id
        parent_id = execute(
            models,
            db,
            uid,
            api_key,
            "product.public.category",
            "create",
            [values],
        )
    return parent_id


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
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
    category_paths = sorted({row["category"] for row in OVERRIDES.values()})
    internal_rows = execute(
        models,
        db,
        uid,
        api_key,
        "product.category",
        "search_read",
        [[("complete_name", "in", category_paths)]],
        {"fields": ["id", "complete_name"], "limit": 100},
    )
    internal_by_path = {row["complete_name"]: row["id"] for row in internal_rows}
    missing = sorted(set(category_paths) - set(internal_by_path))
    if missing:
        raise RuntimeError(f"Missing internal categories: {missing}")

    public_by_path = {
        path: ensure_public_path(
            models, db, uid, api_key, website["id"], path
        )
        for path in category_paths
    }
    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [sorted(OVERRIDES)],
        {
            "fields": [
                "id",
                "default_code",
                "name",
                "categ_id",
                "public_categ_ids",
                "x_studio_oem_part_number",
                "x_studio_manufacturer",
            ]
        },
    )
    rows = []
    for product in products:
        override = OVERRIDES[product["id"]]
        live_oem = (product.get("x_studio_oem_part_number") or "").upper()
        if override["oem"].upper() not in live_oem:
            raise RuntimeError(
                f"OEM guard failed for product {product['id']}: {live_oem}"
            )
        values = {
            "name": override["name"],
            "categ_id": internal_by_path[override["category"]],
            "public_categ_ids": [(6, 0, [public_by_path[override["category"]]])],
        }
        if override["manufacturer"]:
            values["x_studio_manufacturer"] = override["manufacturer"]
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
                "OEM Part Number": override["oem"],
                "Old Name": product.get("name") or "",
                "New Name": override["name"],
                "Old Category": (
                    product["categ_id"][1] if product.get("categ_id") else ""
                ),
                "New Category": override["category"],
                "Manufacturer": override["manufacturer"],
                "Status": "Updated" if args.apply else "Would update",
                "Source": override["source"],
            }
        )

    OUT_DIR.mkdir(exist_ok=True)
    report_path = OUT_DIR / (
        "exact_category_research_"
        + datetime.now().strftime("%Y%m%d_%H%M%S")
        + ".csv"
    )
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "matched": len(rows),
            "updated": len(rows) if args.apply else 0,
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
