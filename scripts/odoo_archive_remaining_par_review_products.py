from __future__ import annotations

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
    return clean(value).upper().replace(" ", "")


def valid_oem(value: str) -> bool:
    if value.upper() in BAD_OEM_VALUES:
        return False
    if value.upper().startswith("PAR-"):
        return False
    if len(value) < 3:
        return False
    return bool(re.search(r"[A-Z0-9]", value))


def main() -> int:
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

    par_ids = execute(models, db, uid, api_key, "product.template", "search", [[("active", "=", True), ("default_code", "=ilike", "PAR-%")]], {"context": {"active_test": False}, "order": "id asc"})
    all_ids = execute(models, db, uid, api_key, "product.template", "search", [[("default_code", "!=", False)]], {"context": {"active_test": False}})

    existing: dict[str, list[int]] = defaultdict(list)
    for id_chunk in chunks(all_ids):
        rows = execute(models, db, uid, api_key, "product.template", "read", [id_chunk], {"fields": ["id", "default_code"], "context": {"active_test": False}})
        for row in rows:
            code = clean_oem(row.get("default_code"))
            if code:
                existing[code].append(row["id"])

    archive_ids: list[int] = []
    report_rows: list[dict[str, Any]] = []
    for id_chunk in chunks(par_ids):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [id_chunk],
            {"fields": ["id", "default_code", "name", "x_studio_oem_part_number", "x_studio_sub_reference"], "context": {"active_test": False}},
        )
        for product in products:
            old_code = clean(product.get("default_code"))
            oem = clean_oem(product.get("x_studio_oem_part_number"))
            reason = ""
            if not PAR_RE.match(old_code):
                reason = "Skipped - not PAR pattern"
            elif not valid_oem(oem):
                reason = "Archived - no valid OEM part number"
                archive_ids.append(product["id"])
            else:
                duplicate_ids = [pid for pid in existing.get(oem, []) if pid != product["id"]]
                if duplicate_ids:
                    reason = f"Archived - duplicate OEM already used by product(s): {duplicate_ids[:10]}"
                    archive_ids.append(product["id"])
                else:
                    reason = "Skipped - OEM no longer duplicated; review manually"
            report_rows.append({
                "Product ID": product["id"],
                "Internal Reference": old_code,
                "OEM Part Number": oem,
                "Name": product.get("name") or "",
                "Action": "Archived" if product["id"] in archive_ids else "Skipped",
                "Reason": reason,
            })

    archived_ids: list[int] = []
    failed_archive: dict[int, str] = {}
    for product_id in archive_ids:
        try:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"active": False}])
            archived_ids.append(product_id)
        except xmlrpc.client.Fault as exc:
            failed_archive[product_id] = str(exc)

    for row in report_rows:
        product_id = int(row["Product ID"])
        if product_id in failed_archive:
            row["Action"] = "Skipped"
            row["Reason"] = f"Odoo protected product; archive failed: {failed_archive[product_id]}"

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"archived_remaining_par_products_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["Product ID", "Internal Reference", "OEM Part Number", "Name", "Action", "Reason"])
        writer.writeheader()
        writer.writerows(report_rows)

    remaining = execute(models, db, uid, api_key, "product.template", "search_count", [[("active", "=", True), ("default_code", "=ilike", "PAR-%")]], {"context": {"active_test": False}})
    print({
        "active_par_checked": len(par_ids),
        "archived": len(archived_ids),
        "remaining_active_par": remaining,
        "status_counts": dict(Counter(row["Action"] for row in report_rows)),
        "report": str(report_path),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

