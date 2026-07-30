"""Apply reviewed Southern Partner Price proposals to Odoo.

Default is dry-run. With --apply, only rows with:
- Status starting with "Ready For Partner Price Apply"
- numeric Odoo ID
- proposed partner price above cost
- proposed partner price below public Sales Price
- proposed partner gross margin at or above --min-gross-margin

are written to product.template.southern_partner_price.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import socket
import time
from datetime import datetime
from pathlib import Path
from typing import Any
import xmlrpc.client

from odoo_runtime import ApplyGate, connect_legacy
from odoo_runtime.safety import append_audit

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"
PARTNER_PRICE_FIELD = "southern_partner_price"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ[key.strip()] = value.strip().strip('"').strip("'")


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    return connect_legacy(ENV_PATH)


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    last_exc: Exception | None = None
    for attempt in range(1, 4):
        try:
            return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})
        except (OSError, TimeoutError, xmlrpc.client.ProtocolError) as exc:
            last_exc = exc
            if attempt == 3:
                raise
            time.sleep(2 * attempt)
    raise last_exc or RuntimeError("Unknown Odoo XML-RPC failure")


def parse_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def gross_margin(price: float, cost: float) -> float:
    if price <= 0:
        return 0.0
    return (price - cost) / price


def southern_company_id(models, db, uid, api_key, explicit_company_id: int | None) -> int:
    if explicit_company_id:
        return explicit_company_id
    env_company_id = os.environ.get("ODOO_COMPANY_ID") or os.environ.get("SOUTHERN_ODOO_COMPANY_ID")
    if env_company_id and env_company_id.isdigit():
        return int(env_company_id)
    company_ids = execute(
        models,
        db,
        uid,
        api_key,
        "res.company",
        "search",
        [[("name", "ilike", "Southern Equipment Company")]],
        {"limit": 1},
    )
    if company_ids:
        return int(company_ids[0])
    return 2


def partner_price_is_company_dependent(models, db, uid, api_key) -> bool:
    fields = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "fields_get",
        [[PARTNER_PRICE_FIELD]],
        {"attributes": ["company_dependent"]},
    )
    return bool(fields.get(PARTNER_PRICE_FIELD, {}).get("company_dependent"))


def product_template_model_id(models, db, uid, api_key) -> int:
    model_ids = execute(
        models,
        db,
        uid,
        api_key,
        "ir.model",
        "search",
        [[("model", "=", "product.template")]],
        {"limit": 1},
    )
    if not model_ids:
        raise SystemExit("Could not resolve product.template model id.")
    return int(model_ids[0])


def apply_company_dependent_partner_prices(models, db, uid, api_key, rows: list[dict[str, Any]], company_id: int) -> None:
    updates = [
        {
            "product_id": int(row["Product ID"]),
            "partner_price": round(float(row["Proposed Partner Price"]), 2),
        }
        for row in rows
    ]
    action_name = f"Temporary apply Southern Partner Price {datetime.now().strftime('%Y%m%d_%H%M%S')}"
    updates_json = json.dumps(updates)
    code = f"""
import json
updates = json.loads({updates_json!r})
company_key = str({company_id})
for update in updates:
    env.cr.execute(
        \"\"\"
        UPDATE product_template
           SET {PARTNER_PRICE_FIELD} = COALESCE({PARTNER_PRICE_FIELD}, '{{}}'::jsonb)
               || jsonb_build_object(%s, %s::numeric)
         WHERE id = %s
        \"\"\",
        (company_key, update["partner_price"], update["product_id"]),
    )
env.cr.commit()
"""
    action_id = execute(
        models,
        db,
        uid,
        api_key,
        "ir.actions.server",
        "create",
        [
            {
                "name": action_name,
                "model_id": product_template_model_id(models, db, uid, api_key),
                "state": "code",
                "code": code,
            }
        ],
    )
    try:
        execute(models, db, uid, api_key, "ir.actions.server", "run", [[action_id]])
    finally:
        execute(models, db, uid, api_key, "ir.actions.server", "unlink", [[action_id]])


def verify_partner_prices(models, db, uid, api_key, rows: list[dict[str, Any]], company_id: int) -> None:
    if not rows:
        return
    expected = {int(row["Product ID"]): round(float(row["Proposed Partner Price"]), 2) for row in rows}
    read_rows = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [list(expected)],
        {
            "fields": ["id", PARTNER_PRICE_FIELD],
            "context": {"allowed_company_ids": [company_id], "active_test": False},
        },
    )
    mismatches = []
    for product in read_rows:
        actual = round(float(product.get(PARTNER_PRICE_FIELD) or 0.0), 2)
        wanted = expected[int(product["id"])]
        if actual != wanted:
            mismatches.append(f"{product['id']}: expected {wanted:.2f}, got {actual:.2f}")
    if mismatches:
        raise SystemExit("Partner price verification failed: " + "; ".join(mismatches))


def load_ready_rows(paths: list[Path], min_gross_margin: float) -> list[dict[str, Any]]:
    ready: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if not (row.get("Status") or "").startswith("Ready For Partner Price Apply"):
                    continue
                product_id_text = (row.get("ID") or "").strip()
                if not product_id_text.isdigit():
                    continue
                product_id = int(product_id_text)
                cost = parse_float(row.get("Cost"))
                retail = parse_float(row.get("Current Sales Price"))
                proposed = parse_float(row.get("Proposed Partner Price"))
                if cost is None or retail is None or proposed is None:
                    continue
                if proposed <= cost or proposed >= retail:
                    continue
                if gross_margin(proposed, cost) < min_gross_margin:
                    continue
                if product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                ready.append(
                    {
                        "Product ID": product_id,
                        "Internal Reference": row.get("Internal Reference", ""),
                        "Name": row.get("Name", ""),
                        "Cost": cost,
                        "Current Sales Price": retail,
                        "Current Partner Price": parse_float(row.get("Current Partner Price")) or 0.0,
                        "Proposed Partner Price": proposed,
                        "Partner Gross Margin %": round(gross_margin(proposed, cost) * 100, 1),
                        "Partner Discount %": row.get("Partner Discount %", ""),
                        "Status": row.get("Status", ""),
                        "Proposal File": str(path),
                    }
                )
    return ready


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed Southern Partner Price proposal CSVs to Odoo.")
    parser.add_argument("proposal_csv", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--min-gross-margin", type=float, default=0.15)
    parser.add_argument("--company-id", type=int, default=None, help="Odoo company id for Partner Price verification/writes.")
    args = parser.parse_args()

    ready_rows = load_ready_rows(args.proposal_csv, args.min_gross_margin)
    db, uid, api_key, models = connect()
    company_id = southern_company_id(models, db, uid, api_key, args.company_id)
    company_dependent = partner_price_is_company_dependent(models, db, uid, api_key)

    applied = 0
    if args.apply and ready_rows:
        gate = ApplyGate("partner-price-proposals", True, args.confirm, args.reason, args.max_records)
        gate.authorize(len(ready_rows))
        append_audit(
            ROOT / "outputs" / "write_audit" / "odoo_writes.jsonl",
            gate.audit_row(ready_rows, len(ready_rows)),
        )
        if company_dependent:
            apply_company_dependent_partner_prices(models, db, uid, api_key, ready_rows, company_id)
            applied = len(ready_rows)
        else:
            for row in ready_rows:
                execute(
                    models,
                    db,
                    uid,
                    api_key,
                    "product.template",
                    "write",
                    [[row["Product ID"]], {PARTNER_PRICE_FIELD: row["Proposed Partner Price"]}],
                )
                applied += 1
        verify_partner_prices(models, db, uid, api_key, ready_rows, company_id)
    for row in ready_rows:
        row["Company ID"] = company_id
        row["Write Mode"] = "company_dependent_sql" if company_dependent else "orm_write"
        if args.apply:
            row["Verified"] = "yes"
        else:
            row["Verified"] = ""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"partner_price_proposals_apply_{stamp}.csv"
    fields = [
        "Product ID",
        "Internal Reference",
        "Name",
        "Cost",
        "Current Sales Price",
        "Current Partner Price",
        "Proposed Partner Price",
        "Partner Gross Margin %",
        "Partner Discount %",
        "Status",
        "Proposal File",
        "Company ID",
        "Write Mode",
        "Verified",
    ]
    with report_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(ready_rows)

    print(
        {
            "mode": "apply" if args.apply else "dry_run",
            "proposal_files": len(args.proposal_csv),
            "ready_rows": len(ready_rows),
            "applied": applied,
            "company_id": company_id,
            "write_mode": "company_dependent_sql" if company_dependent else "orm_write",
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
