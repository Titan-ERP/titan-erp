from __future__ import annotations

import csv
import os
import re
import sys
import xmlrpc.client
from collections import Counter
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
MISSING = ROOT / "odoo_imports/product_master/review_reports/odoo_missing_internal_reference_details.csv"
PLAN = ROOT / "odoo_imports/product_master/review_reports/odoo_missing_reference_extraction_plan.csv"
RESULTS = ROOT / "odoo_imports/product_master/review_reports/odoo_missing_reference_extraction_results.csv"


CODE_RE = re.compile(r"^([A-Z0-9][A-Z0-9./-]{2,})\s+(.+)$", re.IGNORECASE)


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def clean_spaces(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def title_preserve_codes(value: str) -> str:
    words = []
    for word in clean_spaces(value).split(" "):
        if any(char.isdigit() for char in word) or word.isupper():
            words.append(word.upper())
        else:
            words.append(word.capitalize())
    return " ".join(words)


def infer_name_and_category(raw_description: str) -> tuple[str, str]:
    desc = clean_spaces(raw_description)
    lower = desc.lower()
    desc = desc.replace("Water Seperator", "Fuel/Water Separator").replace("Water Separator", "Fuel/Water Separator")
    if "hydraulic filter" in lower or "hydr filter" in lower:
        return "Hydraulic Filter", "Parts / Filters / Hydraulic Filters"
    if "fuel/water separator" in desc.lower() or "water seperator" in lower or "water separator" in lower:
        return "Fuel/Water Separator", "Parts / Filters / Fuel Water Separators"
    if "fuel filter" in lower:
        return "Fuel Filter", "Parts / Filters / Fuel Filters"
    if "oil filter" in lower:
        return "Engine Oil Filter", "Parts / Filters / Engine Oil Filters"
    if "air filter" in lower:
        return "Air Filter", "Parts / Filters / Air Filters"
    if "filter" in lower:
        return title_preserve_codes(desc), "Parts / Filters"
    if "seal kit" in lower:
        return title_preserve_codes(desc), "Parts / Seals / Hydraulic Seal Kits"
    if "seal" in lower:
        return title_preserve_codes(desc), "Parts / Seals"
    if "bearing" in lower or "brg" in lower:
        return title_preserve_codes(desc).replace(" Brg", " Bearing"), "Parts / Bearings"
    if "starter" in lower or "alternator" in lower or "regulator" in lower or "battery" in lower:
        return title_preserve_codes(desc), "Parts / Electrical"
    if "sprocket" in lower or "shaft" in lower or "clutch" in lower or "u-joint" in lower:
        return title_preserve_codes(desc), "Parts / Driveline"
    if "coupler" in lower or "swivel" in lower or "hydraulic" in lower:
        return title_preserve_codes(desc), "Parts / Hydraulic"
    if any(term in lower for term in ["bolt", "washer", "cotter pin", "pin", "nut", "clamp"]):
        return title_preserve_codes(desc), "Parts / Hardware"
    if "blade" in lower or "tooth" in lower or "edge" in lower:
        return title_preserve_codes(desc), "Parts / Ground Engaging Tools"
    return title_preserve_codes(desc), ""


def main() -> None:
    apply = "--apply" in sys.argv
    rows = []
    with MISSING.open(newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            if row["Category"]:
                continue
            if row["Storable"] != "True":
                continue
            match = CODE_RE.match(row["Name"])
            if not match:
                continue
            code = match.group(1).strip().upper()
            description = match.group(2).strip()
            new_name, category = infer_name_and_category(description)
            rows.append({**row, "Extracted Reference": code, "Clean Name": new_name, "Inferred Category": category})

    code_counts = Counter(row["Extracted Reference"] for row in rows)
    plan_rows = []
    for row in rows:
        if code_counts[row["Extracted Reference"]] > 1:
            action = "Review duplicate extracted reference"
        elif not row["Inferred Category"]:
            action = "Review missing inferred category"
        else:
            action = "Update"
        plan_rows.append(
            {
                "Action": action,
                "Product ID": row["Product ID"],
                "Current Name": row["Name"],
                "Extracted Reference": row["Extracted Reference"],
                "Clean Name": row["Clean Name"],
                "Inferred Category": row["Inferred Category"],
                "Reason": "Unique leading code extracted from product name." if action == "Update" else "Needs manual review before changing.",
            }
        )

    fields = ["Action", "Product ID", "Current Name", "Extracted Reference", "Clean Name", "Inferred Category", "Reason", "Status", "Verified", "Error"]
    with PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(plan_rows)

    if not apply:
        print(f"Planned rows: {len(plan_rows)}")
        print(f"Ready updates: {sum(1 for row in plan_rows if row['Action'] == 'Update')}")
        print(f"Plan: {PLAN}")
        return

    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    category_names = sorted({row["Inferred Category"] for row in plan_rows if row["Action"] == "Update"})
    categories = execute(models, db, uid, api_key, "product.category", "search_read", [[("complete_name", "in", category_names)]], {"fields": ["id", "complete_name"], "limit": len(category_names) + 10})
    category_by_name = {row["complete_name"]: row["id"] for row in categories}

    results = []
    for row in plan_rows:
        result = {**row, "Status": "Skipped", "Verified": "", "Error": ""}
        if row["Action"] != "Update":
            results.append(result)
            continue
        try:
            category_id = category_by_name[row["Inferred Category"]]
            product_id = int(row["Product ID"])
            values = {"default_code": row["Extracted Reference"], "name": row["Clean Name"], "categ_id": category_id}
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], values])
            after = execute(models, db, uid, api_key, "product.template", "read", [[product_id]], {"fields": ["default_code", "name", "categ_id"], "context": {"active_test": False}})[0]
            verified = (
                after.get("default_code") == values["default_code"]
                and after.get("name") == values["name"]
                and after.get("categ_id")
                and after["categ_id"][1] == row["Inferred Category"]
            )
            result["Status"] = "Applied"
            result["Verified"] = "Yes" if verified else "No"
        except Exception as exc:  # noqa: BLE001
            result["Status"] = "Failed"
            result["Verified"] = "No"
            result["Error"] = re.sub(r"\s+", " ", str(exc))[:1000]
        results.append(result)

    with RESULTS.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(results)

    applied = [row for row in results if row["Status"] == "Applied"]
    failed = [row for row in applied if row["Verified"] != "Yes"]
    print(f"Applied: {len(applied)}")
    print(f"Verified: {len(applied) - len(failed)}/{len(applied)}")
    print(f"Skipped/review: {sum(1 for row in results if row['Status'] == 'Skipped')}")
    print(f"Results: {RESULTS}")
    if failed:
        raise SystemExit("Some extracted-reference updates failed verification.")


if __name__ == "__main__":
    main()
