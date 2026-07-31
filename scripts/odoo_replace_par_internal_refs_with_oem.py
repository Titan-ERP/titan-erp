from __future__ import annotations

import argparse
import csv
import os
import re
import socket
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"
PAR_RE = re.compile(r"^PAR-[A-Z0-9_-]+$", re.I)
BAD_OEM_VALUES = {"", "N/A", "NA", "NONE", "UNKNOWN", "TBD", "VENDOR TBD", "OEM", "PART", "MISC", "-", "--", "."}


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[int], size: int = 500):
    for index in range(0, len(values), size):
        yield values[index:index + size]


def clean(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def clean_oem(value: Any) -> str:
    text = clean(value).upper()
    text = text.replace(" ", "")
    return text


def valid_oem(value: str) -> bool:
    if value.upper() in BAD_OEM_VALUES:
        return False
    if value.upper().startswith("PAR-"):
        return False
    if len(value) < 3:
        return False
    if not re.search(r"[A-Z0-9]", value):
        return False
    return True


def read_products(models, db, uid, api_key, ids: list[int], fields: list[str]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for id_chunk in chunks(ids):
        products.extend(execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": fields, "context": {"active_test": False}}))
    return products


def main() -> int:
    parser = argparse.ArgumentParser(description="Replace generated PAR internal references with clear OEM part numbers where safe.")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--include-archived", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    socket.setdefaulttimeout(90)
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    fields_get = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["string", "readonly"]})
    if "x_studio_oem_part_number" not in fields_get:
        raise SystemExit("Missing x_studio_oem_part_number field on product.template")
    writable_sub_reference = "x_studio_sub_reference" in fields_get and not fields_get["x_studio_sub_reference"].get("readonly")

    domain: list[Any] = [("default_code", "=ilike", "PAR-%")]
    if not args.include_archived:
        domain.append(("active", "=", True))
    par_ids = execute(models, db, uid, api_key, "product.template", "search", [domain], {"context": {"active_test": False}, "order": "id asc", "limit": args.limit or 0})
    all_ids = execute(models, db, uid, api_key, "product.template", "search", [[("default_code", "!=", False)]], {"context": {"active_test": False}})

    all_products = read_products(models, db, uid, api_key, all_ids, ["id", "default_code"])
    existing_code_to_ids: dict[str, list[int]] = defaultdict(list)
    for product in all_products:
        code = clean_oem(product.get("default_code"))
        if code:
            existing_code_to_ids[code].append(product["id"])

    products = read_products(
        models,
        db,
        uid,
        api_key,
        par_ids,
        ["id", "default_code", "name", "active", "x_studio_oem_part_number", "x_studio_sub_reference"],
    )
    candidate_oems = [clean_oem(product.get("x_studio_oem_part_number")) for product in products]
    candidate_counts = Counter(oem for oem in candidate_oems if oem)

    rows: list[dict[str, Any]] = []
    updates: list[tuple[int, dict[str, Any]]] = []
    for product in products:
        old_code = clean(product.get("default_code"))
        oem = clean_oem(product.get("x_studio_oem_part_number"))
        status = "Ready"
        notes = ""
        values: dict[str, Any] = {}
        if not PAR_RE.match(old_code):
            status = "Skipped - not PAR pattern"
        elif not valid_oem(oem):
            status = "Needs Review - missing/invalid OEM"
            notes = "OEM Part Number is blank, placeholder, too short, or still PAR-like."
        elif candidate_counts[oem] > 1:
            status = "Needs Review - duplicate OEM among PAR products"
            notes = f"{candidate_counts[oem]} PAR products have OEM {oem}."
        else:
            existing_ids = [pid for pid in existing_code_to_ids.get(oem, []) if pid != product["id"]]
            if existing_ids:
                status = "Needs Review - OEM already used as Internal Reference"
                notes = f"Existing product(s) already use {oem}: {existing_ids[:10]}"
            else:
                values["default_code"] = oem
                if writable_sub_reference:
                    sub_ref = clean(product.get("x_studio_sub_reference"))
                    if old_code and old_code not in sub_ref:
                        values["x_studio_sub_reference"] = f"{sub_ref}; Former Internal Reference: {old_code}" if sub_ref else f"Former Internal Reference: {old_code}"
                updates.append((product["id"], values))
                status = "Updated" if args.apply else "Would Update"

        rows.append({
            "Product ID": product["id"],
            "Old Internal Reference": old_code,
            "OEM Part Number": oem,
            "Name": product.get("name") or "",
            "Active": product.get("active"),
            "Status": status,
            "Notes": notes,
        })

    if args.apply:
        for product_id, values in updates:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], values])

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"par_internal_reference_oem_fix_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Old Internal Reference", "OEM Part Number", "Name", "Active", "Status", "Notes"])
        writer.writeheader()
        writer.writerows(rows)

    print({
        "mode": "apply" if args.apply else "dry_run",
        "scope": "active only" if not args.include_archived else "active + archived",
        "par_products_checked": len(products),
        "safe_updates": len(updates),
        "status_counts": dict(Counter(row["Status"] for row in rows)),
        "report": str(report_path),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
