"""Conservatively reclassify active products from Miscellaneous.

Default mode is a dry run.  Rules intentionally require strong wording in the
product name; ambiguous products remain in Miscellaneous for later review.
When applied, both the internal and public website categories are updated.
"""

from __future__ import annotations

import argparse
import csv
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
import xmlrpc.client


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "outputs"
MISC_PATH = "Parts / Miscellaneous"


RULES = [
    ("Services", r"^\s*p/up\s*&\s*delivery\s*$"),
    (
        "Parts / Electrical",
        r"\b(?:ammeter|voltmeter|gauge|instrument|tractormeter|tach(?:o)?(?:meter)?|"
        r"hourmeter|bulb|mini lamps?|distributor|wiring harness|harness|spark plug|"
        r"glow plug|heater plug|core plug heater|engine block heater|heater cord|"
        r"magnetic heater|tank heater|resistor|circuit board|dashboard panel lights|"
        r"dashboard panel cover|dash panel|bulb holder|wire spool|contact set|"
        r"harnesses|primary ignition wire|tune up kit|assorted fuses?|blade fuse|"
        r"pre insulated terminal|terminal kit|head light bulb|preheater)\b|"
        r"^S\.119(?:901|902|904|905|906|907|944|945)$",
    ),
    (
        "Parts / Engine",
        r"\b(?:belt tensioner|exhaust manifold|exhaust extension pipe|"
        r"exhaust pipe|pipe, exhaust|exhaust manifold stud|"
        r"exhaust manifold fire ring|exhaust kit)\b",
    ),
    (
        "Parts / Filters / Air Filters",
        r"\b(?:pre cleaner|oil bath element|oil bath seal ring)\b",
    ),
    (
        "Parts / Filters / Fuel Filters",
        r"^\s*bf940\s*$",
    ),
    (
        "Parts / Hydraulic",
        r"\b(?:parker|pioneer)\b.*\b(?:hydraulic|karrykrimp|parkkrimp|"
        r"minikrimp|die set|die ring|pump unit|hose cutting machine)\b",
    ),
    ("Parts / Ground Engaging Tools", r"\bcaterpillar cutting edge\b"),
    (
        "Parts / Hardware",
        r"\b(?:grease nipples?|screen door roller assembly)\b",
    ),
    ("Parts / Shop Supplies", r"\bdisplay stand only\b"),
    ("Parts / Cooling", r"\bhose,\s*by-?pass\b"),
    ("Parts / Seals / Wheel Seals", r"\b(?:wheel seal|seal wheel)\b"),
    (
        "Parts / Seals / Oil Seals",
        r"\b(?:oil seal|double lip seal|rear main seal|axle shaft seal|"
        r"planter drive seal|push rod seal|repair sleeve/rear seal|collar seal|"
        r"seal \(lip type\)|outer axle housing|gasket / rear seal)\b",
    ),
    (
        "Parts / Seals / Hydraulic Seals",
        r"\b(?:wiper seal|seal wiper|rod seal|piston seal|u-packing|"
        r"back ?up ring|b/up ring|wear ring|dust seal|seal, dust|"
        r"centering bonded seal)\b",
    ),
    ("Parts / Filters / Cab Filters", r"\b(?:cab filter|ac filter)\b"),
    (
        "Parts / Filters / Air Filters",
        r"\b(?:air filter|outer air|outer elem(?:ent)?|inner air|inner elem(?:ent)?|"
        r"air cleaner|precleaner)\b",
    ),
    (
        "Parts / Filters / Fuel Water Separators",
        r"\b(?:fuel/water separator|fuel sep|water sep(?:arator|er|erator)|seperator)\b",
    ),
    (
        "Parts / Filters / Hydraulic Filters",
        r"\b(?:hydraulic filter|hydrauli filter|hydraulic fil|hyydr filter|"
        r"charge filter|pilot filter|filter, power steering|hydraulic strainer)\b",
    ),
    (
        "Parts / Filters / Engine Oil Filters",
        r"\b(?:engine oil filter|oil filter)\b",
    ),
    (
        "Parts / Electrical",
        r"\b(?:circuit breaker|back ?up alarm|battery cable|battery terminal|"
        r"copper lugs?|diode|discon(?:nect)? swit|d twin light|fuel gauge|"
        r"fuel sender|fuel sensor|hour meter|hr meter|inner light|lamp kit|"
        r"light grille|rear lamp|rh light|regulator|temp(?:erature)? gauge|"
        r"temp(?:erature)? sensor|temp(?:erature)? sender|water temp\\. sensor|"
        r"volt meter|spark plug wire|breaker points)\b",
    ),
    (
        "Parts / Cooling",
        r"\b(?:radiator hose|radia hose|rad cap|top hose|bottom hose|"
        r"by ?pass hose|blade fan|anti-freeze tester)\b",
    ),
    (
        "Parts / Driveline",
        r"\b(?:u-joint|drive ?line|dr shaft|clutch|yoke|ag chain|"
        r"chain repair kit|#60 16ft chain|rear chain assy)\b",
    ),
    (
        "Parts / Linkage",
        r"\b(?:tie rod|top ?link|lift link|steering arm|steering joint|"
        r"ball joint|cat [i1] ball|chain stabilizer|clevis kit|leveling box|"
        r"leveling assembly|lift shaft|throttle linkage)\b",
    ),
    (
        "Parts / Hydraulic / Hydraulic Adapters",
        r"\b(?:hose fitting|bulk head fitting|male pipe|female pipe|union|"
        r"code 61|fittings?)\b|^3/8mftoi/2m$",
    ),
    (
        "Parts / Hydraulic / Hydraulic Couplers",
        r"\bfemale flat face\b",
    ),
    (
        "Parts / Hydraulic",
        r"\b(?:cap hydraulic tank|hydraulic cap)\b",
    ),
    (
        "Parts / Hydraulic / Hydraulic Valves",
        r"\b(?:lock valve|relief valve)\b",
    ),
    (
        "Parts / Ground Engaging Tools",
        r"\b(?:box blade shank|alabama sweep|sweep|tooth)\b",
    ),
    (
        "Parts / Implements",
        r"\b(?:rotary cutter blade|blade, rotary cutter|blade-mower|mower blade|"
        r"hay fluffer teeth)\b",
    ),
    (
        "Parts / Fuel System",
        r"\b(?:carburetor|carb\. kit|pump, assy fuel|fuel pump|feed pump|"
        r"fuel bowl|fuel drain plug|fuel injector pipe|fuel lift pump|fuel line|"
        r"fuel shutoff valve|fuel tap|hand primer pump|inline fuel|"
        r"tube fuel|fuel injection return|diesel locking cap)\b",
    ),
    (
        "Parts / Engine",
        r"\b(?:muffler|breather|rain cap|weather cap|piston ring|exhaust elbow|"
        r"exhaust gasket|head gasket|gasket, head cover|heater dipstick|"
        r"silicon heater pad)\b|"
        r"\bpiston\s*$",
    ),
    ("Parts / Bearings", r"\b(?:bearing|brg|cone,? roller)\b"),
    ("Parts / Paint", r"\b(?:spray can)\b"),
    (
        "Parts / Brakes",
        r"\b(?:brake kit|brake shoe|brake band|break band|master cylinder|"
        r"wheel cylinder)\b",
    ),
    (
        "Parts / Shop Supplies",
        r"\b(?:brake cleaner|choke and carb\. cleaner|circuit cleaner|"
        r"def 2\.5 gal|electrical contact and circuit cleaner|"
        r"pelican pen flashlight|silicone|terminal wire brush)\b",
    ),
    ("Parts / Hardware", r"\b(?:pop rivet|rivit|wheel stud)\b"),
    (
        "Parts / Cab",
        r"\b(?:grille|hood latch|mirror|panel assy|seat slide|seat spring|"
        r"seat, michigan style)\b",
    ),
]

DIMENSIONAL_SEAL = re.compile(
    r"^\s*\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?\s*x\s*\d+(?:\.\d+)?\s*$",
    re.IGNORECASE,
)


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def classify(name: str) -> tuple[str, str] | None:
    if DIMENSIONAL_SEAL.fullmatch(name):
        return "Parts / Seals / Oil Seals", "three-dimensional seal size"
    matches = [
        (category, pattern)
        for category, pattern in RULES
        if re.search(pattern, name, flags=re.IGNORECASE)
    ]
    categories = {category for category, _ in matches}
    if len(categories) != 1:
        return None
    category = matches[0][0]
    return category, matches[0][1]


def ensure_public_path(
    models, db, uid, api_key, website_id: int, complete_name: str, apply: bool
) -> int | None:
    parent_id = None
    for name in [part.strip() for part in complete_name.split("/") if part.strip()]:
        domain = [
            ("name", "=", name),
            ("parent_id", "=", parent_id or False),
            "|",
            ("website_id", "=", website_id),
            ("website_id", "=", False),
        ]
        ids = execute(
            models,
            db,
            uid,
            api_key,
            "product.public.category",
            "search",
            [domain],
            {"limit": 1},
        )
        if ids:
            parent_id = ids[0]
            continue
        if not apply:
            return None
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
        {"fields": ["id", "name"], "limit": 100, "order": "id"},
    )
    website = next(
        (item for item in websites if "southern" in item["name"].lower()),
        websites[0],
    )

    internal_categories = execute(
        models,
        db,
        uid,
        api_key,
        "product.category",
        "search_read",
        [[("complete_name", "in", sorted({category for category, _ in RULES} | {"Parts / Seals / Oil Seals"}))]],
        {"fields": ["id", "complete_name"], "limit": 100},
    )
    internal_by_path = {
        category["complete_name"]: category["id"] for category in internal_categories
    }
    if args.apply and "Parts / Brakes" not in internal_by_path:
        parent = execute(
            models,
            db,
            uid,
            api_key,
            "product.category",
            "search_read",
            [[("complete_name", "=", "Parts")]],
            {
                "fields": [
                    "id",
                    "property_account_income_categ_id",
                    "property_account_expense_categ_id",
                    "property_valuation",
                    "property_stock_valuation_account_id",
                    "account_stock_variation_id",
                ],
                "limit": 1,
            },
        )[0]
        values = {
            "name": "Brakes",
            "parent_id": parent["id"],
            "property_valuation": parent.get("property_valuation") or "real_time",
        }
        for field in [
            "property_account_income_categ_id",
            "property_account_expense_categ_id",
            "property_stock_valuation_account_id",
            "account_stock_variation_id",
        ]:
            if parent.get(field):
                values[field] = parent[field][0]
        internal_by_path["Parts / Brakes"] = execute(
            models,
            db,
            uid,
            api_key,
            "product.category",
            "create",
            [values],
        )

    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_read",
        [
            [
                ("active", "=", True),
                ("categ_id.complete_name", "=", MISC_PATH),
            ]
        ],
        {
            "fields": [
                "id",
                "default_code",
                "name",
                "list_price",
                "categ_id",
                "public_categ_ids",
            ],
            "limit": 0,
            "order": "id",
        },
    )

    public_by_path: dict[str, int | None] = {}
    rows = []
    updates = []
    for product in products:
        result = classify(product.get("name") or "")
        if not result:
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Old Category": MISC_PATH,
                    "New Category": "",
                    "Status": "Needs review",
                    "Rule": "",
                }
            )
            continue
        target_path, reason = result
        internal_id = internal_by_path.get(target_path)
        if not internal_id:
            rows.append(
                {
                    "Product ID": product["id"],
                    "Internal Reference": product.get("default_code") or "",
                    "Name": product.get("name") or "",
                    "Old Category": MISC_PATH,
                    "New Category": target_path,
                    "Status": "Skipped - missing internal category",
                    "Rule": reason,
                }
            )
            continue
        if target_path == "Services":
            status = (
                "Moved to Services and hidden from parts storefront"
                if args.apply
                else "Would move to Services and hide from parts storefront"
            )
            updates.append((product["id"], internal_id, None))
        elif target_path not in public_by_path:
            public_by_path[target_path] = ensure_public_path(
                models,
                db,
                uid,
                api_key,
                website["id"],
                target_path,
                args.apply,
            )
        public_id = public_by_path.get(target_path)
        if target_path == "Services":
            pass
        elif not public_id and not args.apply:
            status = "Would create website category and reclassify"
        elif not public_id:
            status = "Skipped - missing website category"
        else:
            status = "Reclassified" if args.apply else "Would reclassify"
            updates.append((product["id"], internal_id, public_id))
        rows.append(
            {
                "Product ID": product["id"],
                "Internal Reference": product.get("default_code") or "",
                "Name": product.get("name") or "",
                "Old Category": MISC_PATH,
                "New Category": target_path,
                "Status": status,
                "Rule": reason,
            }
        )

    if args.apply:
        grouped_updates = defaultdict(list)
        for product_id, internal_id, public_id in updates:
            grouped_updates[(internal_id, public_id)].append(product_id)
        for (internal_id, public_id), product_ids in grouped_updates.items():
            for offset in range(0, len(product_ids), 100):
                execute(
                    models,
                    db,
                    uid,
                    api_key,
                    "product.template",
                    "write",
                    [
                        product_ids[offset : offset + 100],
                        {
                            "categ_id": internal_id,
                            "public_categ_ids": [
                                (6, 0, [public_id] if public_id else [])
                            ],
                        },
                    ],
                )

    OUT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = OUT_DIR / f"miscellaneous_reclassification_{stamp}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Old Category",
                "New Category",
                "Status",
                "Rule",
            ],
        )
        writer.writeheader()
        writer.writerows(rows)

    summary = Counter(
        row["New Category"] for row in rows if row.get("New Category")
    )
    remaining = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_count",
        [
            [
                ("active", "=", True),
                ("categ_id.complete_name", "=", MISC_PATH),
            ]
        ],
    )
    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "reviewed": len(products),
            "matched": len(rows),
            "updated": len(updates) if args.apply else 0,
            "remaining_miscellaneous": remaining,
            "by_category": dict(sorted(summary.items())),
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
