import csv
import os
import re
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_WIP = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_wip_snapshot_2026_07_25.csv"
OUT_DIR = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26"
OUT = OUT_DIR / "all_available_shop_boss_wip_sales_order_alignment_results.csv"
SUMMARY = OUT_DIR / "all_available_shop_boss_wip_sales_order_alignment_summary.md"

COMPANY_NAME = "Southern Equipment Company (Laurel)"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, key, model, method, args, kwargs or {})


def execute_void_ok(models, db, uid, key, model, method, args, kwargs=None):
    try:
        return execute(models, db, uid, key, model, method, args, kwargs)
    except xmlrpc.client.Fault as exc:
        if "cannot marshal None unless allow_none is enabled" in str(exc):
            return None
        raise


def cents(value):
    return int(round(float(str(value or "0").replace(",", "")) * 100))


def read_wip():
    rows = {}
    with SHOP_WIP.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            kind = str(row.get("WIP Type", "")).strip().upper()
            number = str(row.get("WIP Number", "")).strip()
            if not kind or not number:
                continue
            rows[f"{kind}{number}"] = row
    return rows


def extract_ref(order):
    text = " ".join(str(order.get(field) or "") for field in ("client_order_ref", "origin", "name"))
    match = re.search(r"\b(RO|PS)\s*#?\s*(\d+)\b", text, flags=re.I)
    return f"{match.group(1).upper()}{match.group(2)}" if match else ""


def main():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company_id = execute(models, db, uid, api_key, "res.company", "search", [[("name", "=", COMPANY_NAME)]], {"limit": 1})[0]
    wip = read_wip()
    orders = execute(
        models,
        db,
        uid,
        api_key,
        "sale.order",
        "search_read",
        [
            [
                ("company_id", "=", company_id),
                ("date_order", ">=", "2026-01-01"),
                ("date_order", "<", "2026-08-01"),
                ("state", "=", "sale"),
                ("invoice_status", "=", "to invoice"),
            ]
        ],
        {
            "fields": [
                "id",
                "name",
                "partner_id",
                "amount_total",
                "client_order_ref",
                "origin",
                "order_line",
                "invoice_ids",
                "picking_ids",
            ],
            "limit": 500,
        },
    )
    results = []
    for order in orders:
        ref = extract_ref(order)
        wip_row = wip.get(ref)
        if not wip_row:
            continue
        wip_status = str(wip_row.get("WIP Status", "")).strip().upper()
        wip_total = cents(wip_row.get("WIP Total"))
        order_total = cents(order["amount_total"])
        lines = execute(
            models,
            db,
            uid,
            api_key,
            "sale.order.line",
            "read",
            [order["order_line"]],
            {"fields": ["qty_delivered", "qty_invoiced"]},
        )
        delivered = sum(float(line.get("qty_delivered") or 0) for line in lines)
        invoiced = sum(float(line.get("qty_invoiced") or 0) for line in lines)
        action = "skipped"
        reason = ""
        if wip_status == "FINAL":
            reason = "Shop Boss WIP is final; use finalized/closed invoice audit instead of moving back to quote."
        elif order.get("invoice_ids"):
            reason = "Order already has invoices."
        elif delivered or invoiced:
            reason = "Order has delivered or invoiced quantities."
        elif wip_total != order_total:
            reason = "Odoo total does not exactly match Shop Boss WIP total."
        else:
            execute_void_ok(models, db, uid, api_key, "sale.order", "action_cancel", [[order["id"]]])
            execute_void_ok(models, db, uid, api_key, "sale.order", "action_draft", [[order["id"]]])
            action = "moved_back_to_draft_quote"
            reason = "Shop Boss WIP is not final and Odoo had no invoices/deliveries."
        results.append(
            {
                "odoo_order": order["name"],
                "odoo_id": order["id"],
                "partner": order["partner_id"][1] if order.get("partner_id") else "",
                "shop_boss_ref": ref,
                "shop_boss_status": wip_status,
                "shop_boss_total": f"{wip_total / 100:.2f}",
                "odoo_total": f"{order_total / 100:.2f}",
                "invoice_count": len(order.get("invoice_ids") or []),
                "picking_count": len(order.get("picking_ids") or []),
                "delivered_qty": delivered,
                "invoiced_qty": invoiced,
                "action": action,
                "reason": reason,
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "odoo_order",
        "odoo_id",
        "partner",
        "shop_boss_ref",
        "shop_boss_status",
        "shop_boss_total",
        "odoo_total",
        "invoice_count",
        "picking_count",
        "delivered_qty",
        "invoiced_qty",
        "action",
        "reason",
    ]
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    moved = [row for row in results if row["action"] == "moved_back_to_draft_quote"]
    skipped = [row for row in results if row["action"] != "moved_back_to_draft_quote"]
    SUMMARY.write_text(
        "\n".join(
            [
                "# All Available Shop Boss WIP Sales Order Alignment",
                "",
                f"- Candidate confirmed/to-invoice Odoo orders backed by Shop Boss WIP: {len(results)}",
                f"- Moved back to draft quote: {len(moved)}",
                f"- Skipped: {len(skipped)}",
                "",
                "## Moved",
                "",
                *[
                    f"- {row['odoo_order']} {row['partner']} / {row['shop_boss_ref']} / ${float(row['odoo_total']):,.2f} / Shop Boss {row['shop_boss_status']}"
                    for row in moved
                ],
                "",
                "## Skipped",
                "",
                *[
                    f"- {row['odoo_order']} {row['partner']} / {row['shop_boss_ref']} / {row['reason']}"
                    for row in skipped
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(SUMMARY)
    print(f"Moved back to draft: {len(moved)}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
