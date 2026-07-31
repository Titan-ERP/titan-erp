from __future__ import annotations

import argparse
import csv
import os
import socket
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any

from odoo_runtime import ApplyGate, connect_legacy
from odoo_runtime.safety import append_audit

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


def load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}.")
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


def execute(models, db: str, uid: int, api_key: str, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def money(value: Any) -> float:
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return 0.0
    try:
        return round(float(text), 2)
    except ValueError:
        return 0.0


def latest_candidate() -> Path:
    files = sorted(PRICING_DIR.glob("odoo_sparex_price_import_candidate_high_confidence_*.csv"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not files:
        raise SystemExit("No high-confidence Odoo price import candidate found.")
    return files[0]


def connect():
    db, uid, api_key, models = connect_legacy(ENV_PATH)
    return models, db, uid, api_key


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply high-confidence Sparex Sales Price values to Odoo product.template list_price.")
    parser.add_argument("--input", type=Path, default=None)
    parser.add_argument("--apply", action="store_true", help="Actually write prices. Without this flag, this is a dry run.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=100)
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--progress-every", type=int, default=100)
    parser.add_argument("--max-updates", type=int, default=0, help="Limit number of mismatched products to update in this run.")
    args = parser.parse_args()

    input_path = args.input or latest_candidate()
    candidate_rows = list(csv.DictReader(input_path.open(newline="", encoding="utf-8-sig")))
    candidates: dict[str, dict[str, str]] = {}
    for row in candidate_rows:
        sku = row.get("Internal Reference", "").strip().upper()
        price = money(row.get("Sales Price"))
        confidence = row.get("Pricing Confidence", "").strip()
        if not sku.startswith("S.") or price <= 0 or confidence not in {"High", "High - Multi Source"}:
            continue
        candidates[sku] = row

    models, db, uid, api_key = connect()
    product_fields = execute(models, db, uid, api_key, "product.template", "fields_get", [], {"attributes": ["string"]})
    read_fields = ["id", "default_code", "name", "list_price", "active", "sale_ok", "purchase_ok"]
    for optional in ["is_published", "website_published"]:
        if optional in product_fields:
            read_fields.append(optional)

    existing_rows: list[dict[str, Any]] = []
    skus = sorted(candidates)
    for sku_chunk in chunks(skus, 300):
        existing_rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "search_read",
                [[("default_code", "in", sku_chunk)]],
                {"fields": read_fields, "context": {"active_test": False}},
            )
        )
    existing_by_sku = {row.get("default_code", "").strip().upper(): row for row in existing_rows}

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = PRICING_DIR / f"odoo_sparex_price_backup_before_apply_{timestamp}.csv"
    report_path = PRICING_DIR / f"odoo_sparex_website_price_apply_report_{timestamp}.csv"

    report_fields = [
        "Timestamp",
        "Mode",
        "Status",
        "Product ID",
        "Internal Reference",
        "Name",
        "Old Sales Price",
        "New Sales Price",
        "Delta",
        "Active",
        "Sale OK",
        "Published",
        "Evidence URLs",
        "Pricing Notes",
    ]

    update_jobs: list[tuple[int, str, float]] = []
    with backup_path.open("w", newline="", encoding="utf-8-sig") as backup_file, report_path.open("w", newline="", encoding="utf-8-sig") as report_file:
        backup_writer = csv.DictWriter(backup_file, fieldnames=["Product ID", "Internal Reference", "Name", "Old Sales Price", "Active", "Sale OK", "Published"])
        report_writer = csv.DictWriter(report_file, fieldnames=report_fields)
        backup_writer.writeheader()
        report_writer.writeheader()

        for sku in skus:
            candidate = candidates[sku]
            new_price = money(candidate.get("Sales Price"))
            product = existing_by_sku.get(sku)
            if not product:
                report_writer.writerow(
                    {
                        "Timestamp": datetime.now().isoformat(timespec="seconds"),
                        "Mode": "Apply" if args.apply else "Dry Run",
                        "Status": "Missing Product",
                        "Internal Reference": sku,
                        "New Sales Price": f"{new_price:.2f}",
                        "Evidence URLs": candidate.get("Evidence URLs", ""),
                        "Pricing Notes": candidate.get("Pricing Notes", ""),
                    }
                )
                continue

            old_price = money(product.get("list_price"))
            published = bool(product.get("is_published") or product.get("website_published"))
            backup_writer.writerow(
                {
                    "Product ID": product.get("id"),
                    "Internal Reference": sku,
                    "Name": product.get("name", ""),
                    "Old Sales Price": f"{old_price:.2f}",
                    "Active": product.get("active"),
                    "Sale OK": product.get("sale_ok"),
                    "Published": published,
                }
            )
            status = "Unchanged" if abs(old_price - new_price) < 0.005 else ("Pending Update" if not args.apply else "Updated")
            if status != "Unchanged":
                update_jobs.append((int(product["id"]), sku, new_price))
            report_writer.writerow(
                {
                    "Timestamp": datetime.now().isoformat(timespec="seconds"),
                    "Mode": "Apply" if args.apply else "Dry Run",
                    "Status": status,
                    "Product ID": product.get("id"),
                    "Internal Reference": sku,
                    "Name": product.get("name", ""),
                    "Old Sales Price": f"{old_price:.2f}",
                    "New Sales Price": f"{new_price:.2f}",
                    "Delta": f"{new_price - old_price:.2f}",
                    "Active": product.get("active"),
                    "Sale OK": product.get("sale_ok"),
                    "Published": published,
                    "Evidence URLs": candidate.get("Evidence URLs", ""),
                    "Pricing Notes": candidate.get("Pricing Notes", ""),
                }
            )

    if args.apply:
        if args.max_updates:
            update_jobs = update_jobs[: args.max_updates]
        gate = ApplyGate("sparex-website-prices", True, args.confirm, args.reason, args.max_records)
        gate.authorize(len(update_jobs))
        append_audit(
            ROOT / "outputs" / "write_audit" / "odoo_writes.jsonl",
            gate.audit_row(update_jobs, len(update_jobs)),
        )
        completed_writes = 0
        for job_chunk in chunks(update_jobs, args.chunk_size):
            # Different prices per product, so write one product per call. Chunking only controls progress boundaries.
            for product_id, _sku, new_price in job_chunk:
                execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"list_price": new_price}])
                completed_writes += 1
                if args.progress_every and completed_writes % args.progress_every == 0:
                    print(f"Applied {completed_writes}/{len(update_jobs)} price updates", flush=True)

    verify_updated = 0
    verify_unchanged = 0
    if args.apply:
        for sku_chunk in chunks(skus, 300):
            rows = execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "search_read",
                [[("default_code", "in", sku_chunk)]],
                {"fields": ["default_code", "list_price"], "context": {"active_test": False}},
            )
            for row in rows:
                sku = row.get("default_code", "").strip().upper()
                expected = money(candidates.get(sku, {}).get("Sales Price"))
                actual = money(row.get("list_price"))
                if expected and abs(actual - expected) < 0.005:
                    verify_updated += 1
                else:
                    verify_unchanged += 1

    print(f"Input: {input_path}")
    print(f"Backup: {backup_path}")
    print(f"Report: {report_path}")
    print(f"Mode: {'Apply' if args.apply else 'Dry Run'}")
    print(f"Candidate rows: {len(candidate_rows)}")
    print(f"Valid high-confidence candidates: {len(candidates)}")
    print(f"Products found in Odoo: {len(existing_by_sku)}")
    print(f"Rows needing update: {len(update_jobs)}")
    if args.apply:
        print(f"Verified matching price after apply: {verify_updated}")
        print(f"Verification mismatches: {verify_unchanged}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
