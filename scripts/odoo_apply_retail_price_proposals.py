"""Apply reviewed retail price proposals to Odoo products.

Default is dry-run. With --apply, only rows with:
- Status = Ready For Review or Ready For Median Retailer Apply
- numeric Odoo ID
- proposed sales price > minimum price

are written to product.template list_price. The script does not publish products
or change categories; run odoo_apply_website_taxonomy_and_publish.py afterward.
"""

from __future__ import annotations

import argparse
import csv
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
REPORT_DIR = ROOT / "odoo_imports/product_master/pricing"


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


def load_ready_rows(paths: list[Path], min_price: float) -> list[dict[str, Any]]:
    ready: list[dict[str, Any]] = []
    seen_ids: set[int] = set()
    for path in paths:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("Status") not in {"Ready For Review", "Ready For Median Retailer Apply"}:
                    continue
                product_id_text = (row.get("ID") or "").strip()
                if not product_id_text.isdigit():
                    continue
                product_id = int(product_id_text)
                proposed = parse_float(row.get("Proposed Sales Price"))
                if proposed is None or proposed <= min_price:
                    continue
                if product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                ready.append(
                    {
                        "Product ID": product_id,
                        "Internal Reference": row.get("Internal Reference", ""),
                        "Name": row.get("Name", ""),
                        "Current Sales Price": row.get("Current Sales Price", ""),
                        "Proposed Sales Price": proposed,
                        "Sources": row.get("Sources", ""),
                        "Source URLs": row.get("Source URLs", ""),
                        "Proposal File": str(path),
                    }
                )
    return ready


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply reviewed retail price proposal CSVs to Odoo.")
    parser.add_argument("proposal_csv", nargs="+", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--min-price", type=float, default=1.0)
    args = parser.parse_args()

    ready_rows = load_ready_rows(args.proposal_csv, args.min_price)
    db, uid, api_key, models = connect()

    applied = 0
    if args.apply:
        gate = ApplyGate("retail-price-proposals", True, args.confirm, args.reason, args.max_records)
        gate.authorize(len(ready_rows))
        append_audit(
            ROOT / "outputs" / "write_audit" / "odoo_writes.jsonl",
            gate.audit_row(ready_rows, len(ready_rows)),
        )
    for row in ready_rows:
        if args.apply:
            execute(models, db, uid, api_key, "product.template", "write", [[row["Product ID"]], {"list_price": row["Proposed Sales Price"]}])
            applied += 1

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"retail_price_proposals_apply_{stamp}.csv"
    fields = [
        "Product ID",
        "Internal Reference",
        "Name",
        "Current Sales Price",
        "Proposed Sales Price",
        "Sources",
        "Source URLs",
        "Proposal File",
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
            "report": str(report_path),
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

