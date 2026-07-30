from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import xmlrpc.client
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from odoo_runtime import ApplyGate

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "order_refresh"
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"
STATE_PATH = OUT_DIR / "recent_order_sparex_refresh_state.json"
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
    return xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object"), db, uid, api_key


def load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return {"processed": {}}
    return json.loads(STATE_PATH.read_text(encoding="utf-8"))


def save_state(state: dict[str, Any]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")


def rel_id(value: Any) -> int:
    return int(value[0]) if isinstance(value, (list, tuple)) and value else 0


def collect_recent_order_skus(models, db, uid, api_key, since_minutes: int, limit: int) -> list[dict[str, Any]]:
    since = (datetime.now(UTC) - timedelta(minutes=since_minutes)).strftime("%Y-%m-%d %H:%M:%S")
    events: list[dict[str, Any]] = []
    order_specs = [
        ("sale.order", "sale.order.line", "sale_order", "order_line", [("state", "not in", ["cancel"])]),
        ("purchase.order", "purchase.order.line", "purchase_order", "order_line", [("state", "not in", ["cancel"])]),
    ]
    for order_model, line_model, trigger_kind, line_field, extra_domain in order_specs:
        domain = ["|", ("create_date", ">=", since), ("write_date", ">=", since), *extra_domain]
        orders = execute(
            models,
            db,
            uid,
            api_key,
            order_model,
            "search_read",
            [domain],
            {"fields": ["id", "name", "state", line_field, "create_date", "write_date"], "limit": limit, "order": "write_date desc"},
        )
        line_ids = [line_id for order in orders for line_id in (order.get(line_field) or [])]
        if not line_ids:
            continue
        lines = execute(
            models,
            db,
            uid,
            api_key,
            line_model,
            "read",
            [line_ids],
            {"fields": ["id", "order_id", "product_id"]},
        )
        product_ids = list({rel_id(line.get("product_id")) for line in lines if rel_id(line.get("product_id"))})
        if not product_ids:
            continue
        variants = execute(
            models,
            db,
            uid,
            api_key,
            "product.product",
            "read",
            [product_ids],
            {"fields": ["id", "product_tmpl_id", "default_code"]},
        )
        variant_by_id = {row["id"]: row for row in variants}
        tmpl_ids = list({rel_id(row.get("product_tmpl_id")) for row in variants if rel_id(row.get("product_tmpl_id"))})
        templates = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [tmpl_ids],
            {"fields": ["id", "default_code", "name", "southern_source_url"], "context": {"active_test": False}},
        )
        template_by_id = {row["id"]: row for row in templates}
        order_by_id = {order["id"]: order for order in orders}
        for line in lines:
            variant = variant_by_id.get(rel_id(line.get("product_id")), {})
            template = template_by_id.get(rel_id(variant.get("product_tmpl_id")), {})
            sku = str(template.get("default_code") or variant.get("default_code") or "").strip()
            if not sku.startswith("S."):
                continue
            order_id = rel_id(line.get("order_id"))
            order = order_by_id.get(order_id, {})
            events.append(
                {
                    "key": f"{order_model}:{order_id}:{sku}",
                    "trigger_model": order_model,
                    "trigger_kind": trigger_kind,
                    "trigger_id": order_id,
                    "trigger_name": order.get("name") or "",
                    "sku": sku,
                    "product_tmpl_id": template.get("id"),
                    "product_name": template.get("name") or "",
                    "source_url": template.get("southern_source_url") or "",
                }
            )
    unique: dict[str, dict[str, Any]] = {}
    for event in events:
        unique[event["key"]] = event
    return list(unique.values())


def write_sku_file(skus: list[str], timestamp: str) -> Path:
    path = OUT_DIR / f"recent_order_sparex_skus_{timestamp}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["SKU"])
        writer.writeheader()
        for sku in skus:
            writer.writerow({"SKU": sku})
    return path


def run_command(command: list[str], timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=timeout)


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh Sparex products found on recent Sales Orders and Purchase Orders.")
    parser.add_argument("--since-minutes", type=int, default=120)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--force", action="store_true", help="Reprocess events even if they are already in the local state file.")
    parser.add_argument("--skip-retail", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Allow product refresh writes. Default is evidence-only.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=25)
    args = parser.parse_args()

    load_env()
    models, db, uid, api_key = connect()
    state = load_state()
    processed = state.setdefault("processed", {})
    events = collect_recent_order_skus(models, db, uid, api_key, args.since_minutes, args.limit)
    new_events = [event for event in events if args.force or event["key"] not in processed]
    skus = list(dict.fromkeys(event["sku"] for event in new_events))[: args.limit]
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if not skus:
        print("No new recent SO/PO Sparex products to refresh.")
        return 0
    if args.apply:
        ApplyGate("recent-order-sparex-refresh", True, args.confirm, args.reason, args.max_records).authorize(len(skus))

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
    registry_output = ""
    if not args.skip_retail:
        sku_file = write_sku_file(skus, timestamp)
        retail_run = f"recent_order_retail_compare_{timestamp}"
        retail_result = run_command(
            [
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
            ],
            args.timeout,
        )
        retail_csv = str(PRICING_DIR / f"{retail_run}.csv")
        if retail_result.returncode == 0 and Path(retail_csv).exists():
            registry_result = run_command([PYTHON, "scripts/build_price_source_registry.py", retail_csv], args.timeout)
            registry_output = registry_result.stdout.strip() or registry_result.stderr.strip()
        else:
            registry_output = retail_result.stdout.strip() + "\n" + retail_result.stderr.strip()

    status = "done" if dealer_result.returncode == 0 else "error"
    checked_at = datetime.now().isoformat(timespec="seconds")
    if status == "done" and args.apply:
        for event in new_events:
            if event["sku"] in skus:
                processed[event["key"]] = {"sku": event["sku"], "checked_at": checked_at, "trigger": event["trigger_name"]}
        save_state(state)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / f"recent_order_sparex_refresh_{timestamp}.md"
    report_path.write_text(
        "\n".join(
            [
                f"# Recent Order Sparex Refresh - {checked_at}",
                "",
                f"- Recent events found: {len(events)}",
                f"- New events processed: {len(new_events)}",
                f"- Unique SKUs refreshed: {len(skus)}",
                f"- SKUs: {', '.join(skus)}",
                f"- Dealer refresh status: {status}",
                f"- Retail evidence CSV: {retail_csv or 'skipped'}",
                "",
                "## Dealer Output",
                "```text",
                dealer_result.stdout.strip(),
                dealer_result.stderr.strip(),
                "```",
                "",
                "## Retail Registry Output",
                "```text",
                registry_output,
                "```",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Recent order events found: {len(events)}")
    print(f"New events processed: {len(new_events)}")
    print(f"Unique SKUs refreshed: {len(skus)}")
    print(f"Status: {status}")
    print(f"Report: {report_path}")
    return dealer_result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
