import csv
import os
import re
import sys
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_ROS = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_finalized_ro_rows_ytd_2026.csv"
OUT = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "shop_boss_verified_june_ro_invoice_results.csv"
SUMMARY = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "shop_boss_verified_june_ro_invoice_summary.md"

COMPANY_NAME = "Southern Equipment Company (Laurel)"
SALES_JOURNAL_NAME = "Sales"
SERVICE_ACCOUNT_NAME = "Service Revenue"
PARTS_ACCOUNT_NAME = "Parts Revenue"
SALES_TAX_NAME = "MS Sales Tax - 7%"
ROS_TO_CREATE = {"1069", "1102"}


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


def single(rows, label):
    if len(rows) != 1:
        raise SystemExit(f"Expected exactly one {label}; found {len(rows)}")
    return rows[0]


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").replace("\x02", "").replace("\x03", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cents(value):
    return int(money(value) * 100)


def clean_name(value):
    return re.sub(r"[\x00-\x1f]+", "", str(value or "")).strip()


def iso_date(value):
    text = clean_name(value)
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return datetime.strptime(text, "%m/%d/%Y").strftime("%Y-%m-%d")


def norm(value):
    text = clean_name(value).upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def read_shop_rows():
    rows = {}
    with SHOP_ROS.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            number = str(row.get("ro_number", "")).strip()
            if number in ROS_TO_CREATE:
                rows[number] = row
    missing = ROS_TO_CREATE - set(rows)
    if missing:
        raise SystemExit(f"Missing Shop Boss RO rows: {sorted(missing)}")
    return rows


def find_partner(models, db, uid, key, customer):
    target = norm(customer)
    candidates = execute(
        models,
        db,
        uid,
        key,
        "res.partner",
        "search_read",
        [[("name", "ilike", clean_name(customer))]],
        {"fields": ["id", "name"], "limit": 20},
    )
    exact = [row for row in candidates if norm(row["name"]) == target]
    if len(exact) == 1:
        return exact[0]
    tokens = set(target.split())
    scored = []
    for row in candidates:
        score = len(tokens & set(norm(row["name"]).split()))
        if score:
            scored.append((score, row))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    partner_id = execute(models, db, uid, key, "res.partner", "create", [{"name": clean_name(customer), "customer_rank": 1}])
    return {"id": partner_id, "name": clean_name(customer)}


def invoice_totals(models, db, uid, key, invoice_id):
    return execute(
        models,
        db,
        uid,
        key,
        "account.move",
        "read",
        [[invoice_id]],
        {"fields": ["id", "name", "state", "payment_state", "amount_untaxed", "amount_tax", "amount_total"]},
    )[0]


def write_csv(rows):
    fields = [
        "ro_number",
        "customer",
        "final_date",
        "partner_id",
        "partner",
        "invoice_id",
        "invoice",
        "untaxed",
        "tax",
        "total",
        "service_revenue",
        "parts_revenue",
        "sale_order",
        "sale_order_action",
        "status",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


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

    company = single(
        execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY_NAME)]], {"fields": ["id", "name"], "limit": 2}),
        COMPANY_NAME,
    )
    journal = single(
        execute(
            models,
            db,
            uid,
            api_key,
            "account.journal",
            "search_read",
            [[("name", "=", SALES_JOURNAL_NAME), ("company_id", "=", company["id"])]],
            {"fields": ["id", "name"], "limit": 2},
        ),
        SALES_JOURNAL_NAME,
    )
    tax = single(
        execute(
            models,
            db,
            uid,
            api_key,
            "account.tax",
            "search_read",
            [[("name", "=", SALES_TAX_NAME), ("company_id", "=", company["id"])]],
            {"fields": ["id", "name"], "limit": 2},
        ),
        SALES_TAX_NAME,
    )
    service_account = single(
        execute(
            models,
            db,
            uid,
            api_key,
            "account.account",
            "search_read",
            [[("name", "=", SERVICE_ACCOUNT_NAME), ("company_ids", "in", [company["id"]])]],
            {"fields": ["id", "name"], "limit": 2},
        ),
        SERVICE_ACCOUNT_NAME,
    )
    parts_account = single(
        execute(
            models,
            db,
            uid,
            api_key,
            "account.account",
            "search_read",
            [[("name", "=", PARTS_ACCOUNT_NAME), ("company_ids", "in", [company["id"]])]],
            {"fields": ["id", "name"], "limit": 2},
        ),
        PARTS_ACCOUNT_NAME,
    )

    results = []
    for ro, shop in sorted(read_shop_rows().items()):
        ref = f"Shop Boss RO {ro}"
        existing = execute(
            models,
            db,
            uid,
            api_key,
            "account.move",
            "search_read",
            [[("company_id", "=", company["id"]), ("move_type", "=", "out_invoice"), ("state", "!=", "cancel"), "|", ("ref", "ilike", ref), ("invoice_origin", "ilike", ref)]],
            {"fields": ["id", "name"], "limit": 5},
        )
        if existing:
            invoice = invoice_totals(models, db, uid, api_key, existing[0]["id"])
            status = "skipped_existing_invoice"
        else:
            partner = find_partner(models, db, uid, api_key, shop["customer"])
            service_total = money(shop["labor"]) + money(shop["sublet"]) + money(shop["fees"])
            parts_total = money(shop["parts"])
            line_commands = []
            if service_total:
                line_commands.append(
                    (
                        0,
                        0,
                        {
                            "name": f"{ref} - labor/sublet/fees",
                            "quantity": 1.0,
                            "price_unit": float(service_total),
                            "account_id": service_account["id"],
                            "tax_ids": [(6, 0, [tax["id"]])] if money(shop["tax"]) else [(6, 0, [])],
                        },
                    )
                )
            if parts_total:
                line_commands.append(
                    (
                        0,
                        0,
                        {
                            "name": f"{ref} - parts",
                            "quantity": 1.0,
                            "price_unit": float(parts_total),
                            "account_id": parts_account["id"],
                            "tax_ids": [(6, 0, [tax["id"]])] if money(shop["tax"]) else [(6, 0, [])],
                        },
                    )
                )
            invoice_id = execute(
                models,
                db,
                uid,
                api_key,
                "account.move",
                "create",
                [
                    {
                        "move_type": "out_invoice",
                        "partner_id": partner["id"],
                        "company_id": company["id"],
                        "journal_id": journal["id"],
                        "invoice_date": iso_date(shop["final_date"]),
                        "invoice_date_due": iso_date(shop["final_date"]),
                        "invoice_origin": ref,
                        "ref": ref,
                        "invoice_line_ids": line_commands,
                    }
                ],
            )
            invoice = invoice_totals(models, db, uid, api_key, invoice_id)
            expected_untaxed = money(shop["labor"]) + money(shop["parts"]) + money(shop["sublet"]) + money(shop["fees"])
            if cents(invoice["amount_untaxed"]) != cents(expected_untaxed) or cents(invoice["amount_tax"]) != cents(shop["tax"]):
                raise SystemExit(f"Invoice {invoice_id} does not match Shop Boss RO {ro}: {invoice}")
            execute_void_ok(models, db, uid, api_key, "account.move", "action_post", [[invoice_id]])
            invoice = invoice_totals(models, db, uid, api_key, invoice_id)
            status = "posted"

        sale_order_action = ""
        sale_order_name = ""
        orders = execute(
            models,
            db,
            uid,
            api_key,
            "sale.order",
            "search_read",
            [
                [
                    ("company_id", "=", company["id"]),
                    ("state", "=", "draft"),
                    "|",
                    ("client_order_ref", "ilike", f"RO {ro}"),
                    ("origin", "ilike", f"RO {ro}"),
                ]
            ],
            {"fields": ["id", "name", "amount_total"], "limit": 5},
        )
        for order in orders:
            if cents(order["amount_total"]) == cents(money(shop["labor"]) + money(shop["parts"]) + money(shop["sublet"]) + money(shop["fees"]) + money(shop["tax"])):
                execute_void_ok(models, db, uid, api_key, "sale.order", "action_cancel", [[order["id"]]])
                sale_order_name = order["name"]
                sale_order_action = "cancelled_placeholder_quote"
                break

        results.append(
            {
                "ro_number": ro,
                "customer": clean_name(shop["customer"]),
                "final_date": shop["final_date"],
                "partner_id": partner["id"] if not existing else "",
                "partner": partner["name"] if not existing else "",
                "invoice_id": invoice["id"],
                "invoice": invoice["name"],
                "untaxed": f"{float(invoice['amount_untaxed']):.2f}",
                "tax": f"{float(invoice['amount_tax']):.2f}",
                "total": f"{float(invoice['amount_total']):.2f}",
                "service_revenue": f"{float(money(shop['labor']) + money(shop['sublet']) + money(shop['fees'])):.2f}",
                "parts_revenue": f"{float(money(shop['parts'])):.2f}",
                "sale_order": sale_order_name,
                "sale_order_action": sale_order_action,
                "status": status,
            }
        )

    write_csv(results)
    posted = [row for row in results if row["status"] == "posted"]
    SUMMARY.write_text(
        "\n".join(
            [
                "# Shop Boss Verified June RO Invoice Fix",
                "",
                f"- Posted invoices: {len(posted)}",
                f"- Placeholder quotes cancelled: {sum(1 for row in results if row['sale_order_action'])}",
                f"- Service revenue added: ${sum(float(row['service_revenue']) for row in posted):,.2f}",
                f"- Parts revenue added: ${sum(float(row['parts_revenue']) for row in posted):,.2f}",
                f"- Total AR/invoices added: ${sum(float(row['total']) for row in posted):,.2f}",
                "",
                "## Rows",
                "",
                *[
                    f"- RO{row['ro_number']} -> {row['invoice']} ${float(row['total']):,.2f}; quote {row['sale_order']} {row['sale_order_action'] or 'not changed'}"
                    for row in results
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(SUMMARY)


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
