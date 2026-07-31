from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any

from odoo_runtime import ApplyGate

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "automation_reports"
PYTHON = sys.executable


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def connect():
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return models, db, uid, api_key


def write_sku_file(skus: list[str], timestamp: str) -> Path:
    path = PRICING_DIR / f"order_refresh_skus_{timestamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["SKU"])
        writer.writeheader()
        for sku in skus:
            writer.writerow({"SKU": sku})
    return path


def run_command(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


def mark_rows(models, db, uid, api_key, row_ids: list[int], state: str, message: str) -> None:
    if not row_ids:
        return
    values: dict[str, Any] = {
        "state": state,
        "last_attempt_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "last_message": message[:4000],
    }
    if state == "done":
        values["last_done_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    execute(models, db, uid, api_key, "southern.parts.order.refresh.queue", "write", [row_ids, values])


def main() -> int:
    parser = argparse.ArgumentParser(description="Drain Odoo order-triggered Sparex refresh queue.")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--skip-retail", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Allow queue and product writes. Default is read-only.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=20)
    args = parser.parse_args()

    load_env()
    models, db, uid, api_key = connect()

    queue_rows = execute(
        models,
        db,
        uid,
        api_key,
        "southern.parts.order.refresh.queue",
        "search_read",
        [[("state", "=", "pending")]],
        {
            "fields": ["id", "default_code", "refresh_cost", "refresh_retail", "refresh_source", "attempt_count"],
            "order": "priority desc, create_date, id",
            "limit": args.limit,
        },
    )
    if not queue_rows:
        print("No pending order-triggered Sparex refresh rows.")
        return 0

    row_ids = [int(row["id"]) for row in queue_rows]
    skus = list(dict.fromkeys(str(row.get("default_code") or "").strip() for row in queue_rows if str(row.get("default_code") or "").startswith("S.")))
    if not skus:
        if args.apply:
            ApplyGate("order-refresh-queue", True, args.confirm, args.reason, args.max_records).authorize(len(row_ids))
            mark_rows(models, db, uid, api_key, row_ids, "error", "No valid S.% SKUs found in pending queue rows.")
        print("No valid S.% SKUs found.")
        return 1

    if args.apply:
        ApplyGate("order-refresh-queue", True, args.confirm, args.reason, args.max_records).authorize(len(row_ids))
        execute(
            models,
            db,
            uid,
            api_key,
            "southern.parts.order.refresh.queue",
            "write",
            [row_ids, {"state": "running", "attempt_count": 1, "last_attempt_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}],
        )

    dealer_command = [
        PYTHON,
        "scripts/sparex_dealer_portal_sync.py",
        "--delay",
        str(args.delay),
        "--max-errors",
        "3",
    ]
    if args.apply:
        dealer_command.extend(["--apply-cost", "--apply-source-url", "--apply-supplierinfo"])
    for sku in skus:
        dealer_command.extend(["--sku", sku])
    dealer_result = run_command(dealer_command, args.timeout)

    retail_csv = ""
    retail_registry_message = ""
    if not args.skip_retail:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        sku_file = write_sku_file(skus, timestamp)
        retail_run = f"order_triggered_retail_compare_{timestamp}"
        retail_command = [
            PYTHON,
            "scripts/web_research_retail_prices.py",
            "--sku-file",
            str(sku_file),
            "--farmingparts-limit",
            str(len(skus)),
            "--lowe-young-pages",
            "1",
            "--delay",
            str(max(args.delay, 1.0)),
            "--timeout",
            "20",
            "--run-name",
            retail_run,
        ]
        retail_result = run_command(retail_command, args.timeout)
        retail_csv = str(PRICING_DIR / f"{retail_run}.csv")
        if retail_result.returncode == 0 and Path(retail_csv).exists():
            registry_result = run_command([PYTHON, "scripts/build_price_source_registry.py", retail_csv], args.timeout)
            retail_registry_message = registry_result.stdout.strip() or registry_result.stderr.strip()
        else:
            retail_registry_message = retail_result.stderr.strip() or retail_result.stdout.strip()

    state = "done" if dealer_result.returncode == 0 else "error"
    message = "\n".join(
        part
        for part in [
            "Dealer refresh:",
            dealer_result.stdout.strip(),
            dealer_result.stderr.strip(),
            f"Retail evidence CSV: {retail_csv}" if retail_csv else "",
            retail_registry_message,
        ]
        if part
    )
    if args.apply:
        mark_rows(models, db, uid, api_key, row_ids, state, message)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"order_triggered_sparex_refresh_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path.write_text(
        "\n".join(
            [
                f"# Order-Triggered Sparex Refresh - {datetime.now().isoformat(timespec='seconds')}",
                "",
                f"- Queue rows: {len(row_ids)}",
                f"- SKUs: {', '.join(skus)}",
                f"- Dealer command return code: {dealer_result.returncode}",
                f"- Retail evidence CSV: {retail_csv or 'skipped'}",
                f"- Final queue state: {state}",
                "",
                "## Dealer Output",
                "",
                "```text",
                dealer_result.stdout.strip(),
                dealer_result.stderr.strip(),
                "```",
                "",
                "## Retail Registry Output",
                "",
                "```text",
                retail_registry_message,
                "```",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Processed queue rows: {len(row_ids)}")
    print(f"SKUs: {', '.join(skus)}")
    print(f"Queue state: {state}")
    print(f"Report: {report_path}")
    return dealer_result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
