from __future__ import annotations

import argparse
import csv
import http.client
import os
import pathlib
import time
import xmlrpc.client
from datetime import datetime


ROOT = pathlib.Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "odoo_connection.env"


def load_env(path: pathlib.Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value)


def required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def connect():
    load_env(DEFAULT_ENV)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = None
    for attempt in range(6):
        try:
            uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
            break
        except (xmlrpc.client.ProtocolError, http.client.BadStatusLine):
            if attempt == 5:
                raise
            time.sleep(10 * (attempt + 1))
    if not uid:
        raise RuntimeError("Odoo authentication failed")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model, method, args=None, kwargs=None):
    for attempt in range(5):
        try:
            return models.execute_kw(db, uid, api_key, model, method, args or [], kwargs or {})
        except (xmlrpc.client.ProtocolError, http.client.BadStatusLine):
            if attempt == 4:
                raise
            time.sleep(10 * (attempt + 1))


def read_rows(path: pathlib.Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def evidence_status(row: dict[str, str], force_status: str | None = None) -> str:
    if force_status:
        return force_status
    currency = (row.get("Currency") or "").strip().upper()
    if currency and currency != "USD":
        return "currency_review"
    if (row.get("Observed Retail Price") or "").strip():
        return "ready_for_products_agent_review"
    return "queued"


def odoo_datetime(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    value = value.replace("T", " ")
    if len(value) >= 19:
        return value[:19]
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def queue_vals(row: dict[str, str], status: str) -> dict:
    checked = odoo_datetime(row.get("Last Checked"))
    default_code = (row.get("Internal Reference") or row.get("SKU") or row.get("default_code") or "").strip()
    source_name = (row.get("Source") or "Unknown").strip()
    source_url = (row.get("Source URL") or row.get("Price URL") or "").strip()
    price = (row.get("Observed Retail Price") or row.get("Last Observed Price") or "").replace(",", "").strip()
    confidence = (row.get("Confidence") or "0").strip()
    vals = {
        "default_code": default_code,
        "evidence_type": "pricing",
        "status": status,
        "source_name": source_name,
        "source_url": source_url,
        "source_search_url": (row.get("Source Search URL") or row.get("Search URL") or "").strip(),
        "source_title": (row.get("Title") or row.get("Name") or "").strip(),
        "currency_code": (row.get("Currency") or "").strip().upper(),
        "confidence": float(confidence or 0),
        "last_checked_at": checked,
        "notes": (row.get("Notes") or "").strip(),
    }
    if price:
        vals["observed_price"] = float(price)
    if status in {"alternate_source_needed", "blocked", "rate_limited"}:
        vals["blocker_reason"] = vals["notes"] or status.replace("_", " ")
    return vals


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed reviewed evidence rows into the Odoo Parts Evidence Queue.")
    parser.add_argument("csv_path", type=pathlib.Path)
    parser.add_argument("--status", choices=[
        "queued",
        "exact_evidence_found",
        "currency_review",
        "alternate_source_needed",
        "rate_limited",
        "ready_for_products_agent_review",
        "applied",
        "blocked",
        "rejected",
    ])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--update-existing", action="store_true")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    rows = read_rows(args.csv_path)
    if args.limit:
        rows = rows[: args.limit]

    prepared = []
    skipped = 0
    for row in rows:
        status = evidence_status(row, args.status)
        vals = queue_vals(row, status)
        if not vals["default_code"]:
            skipped += 1
            continue
        vals["external_key"] = "|".join(
            [
                vals["default_code"].lower(),
                vals["evidence_type"],
                vals["source_name"].lower(),
                vals["source_url"].lower(),
            ]
        )
        prepared.append(vals)

    existing_by_key = {}
    keys = [vals["external_key"] for vals in prepared]
    for start in range(0, len(keys), 500):
        chunk = keys[start : start + 500]
        existing_rows = execute(
            models,
            db,
            uid,
            api_key,
            "southern.parts.evidence.queue",
            "search_read",
            [[("external_key", "in", chunk)]],
            {"fields": ["id", "external_key"], "limit": len(chunk)},
        )
        for existing in existing_rows:
            existing_by_key[existing["external_key"]] = existing["id"]

    to_create = [vals for vals in prepared if vals["external_key"] not in existing_by_key]
    to_update = [vals for vals in prepared if vals["external_key"] in existing_by_key]

    created = 0
    updated = 0
    for start in range(0, len(to_create), 100):
        chunk = to_create[start : start + 100]
        execute(models, db, uid, api_key, "southern.parts.evidence.queue", "create", [chunk])
        created += len(chunk)

    if args.update_existing:
        for vals in to_update:
            execute(models, db, uid, api_key, "southern.parts.evidence.queue", "write", [[existing_by_key[vals["external_key"]]], vals])
            updated += 1

    print(f"CSV: {args.csv_path}")
    print(f"Rows read: {len(rows)}")
    print(f"Created: {created}")
    print(f"Updated: {updated}")
    print(f"Existing skipped: {len(to_update) - updated}")
    print(f"Skipped: {skipped}")


if __name__ == "__main__":
    main()
