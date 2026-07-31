import csv
import os
import re
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_ALL = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_all_invoice_rows_finalized_closed_2026_07.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_shop_boss_july_ro_invoice_truth_apply_results.csv"

COMPANY_NAME = "Southern Equipment Company (Laurel)"
SALES_JOURNAL_NAME = "Sales"
SERVICE_ACCOUNT_NAME = "Service Revenue"
PARTS_ACCOUNT_NAME = "Parts Revenue"
SALES_TAX_NAME = "MS Sales Tax - 7%"

CREATE_ROS = {"1108", "1109", "1110", "1111", "1082", "1104", "1106"}
UPDATE_EXISTING = {
    "1107": 683,  # Petal Outdoors: Odoo amount was high vs Shop Boss.
    "1112": 629,  # Steven Jeffcoat: amount matches Shop Boss, but was draft/no Shop Boss RO ref.
}
PARTNER_OVERRIDES = {
    "IMSA INTERNATIONAL MANAGEMENT STAFFING AGENCY": "IMSA- INTERNATIONAL MANAGEMENT STAFFING AGENCY",
}


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def execute_void_ok(models, db, uid, api_key, model, method, args, kwargs=None):
    try:
        return execute(models, db, uid, api_key, model, method, args, kwargs)
    except xmlrpc.client.Fault as exc:
        if "cannot marshal None unless allow_none is enabled" in str(exc):
            return None
        raise


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(rows):
    fields = [
        "Status", "Action", "Shop Boss RO", "Customer", "Odoo Partner ID", "Odoo Partner",
        "Odoo Invoice ID", "Odoo Invoice", "Invoice Date", "Untaxed", "Tax", "Total", "Reason",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cents(value):
    return int(money(value) * 100)


def norm(value):
    text = str(value or "").upper().replace("&", " AND ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def single(rows, label):
    if len(rows) != 1:
        raise SystemExit(f"Expected exactly one {label}; found {len(rows)}")
    return rows[0]


def shop_rows_by_ro():
    return {
        row["Shop Boss Number"]: row
        for row in read_csv(SHOP_ALL)
        if row["Shop Boss Type"] == "RO"
    }


def find_or_create_partner(models, db, uid, api_key, customer, apply):
    target = PARTNER_OVERRIDES.get(norm(customer), customer)
    exact = execute(
        models, db, uid, api_key, "res.partner", "search_read",
        [[("name", "=", target)]],
        {"fields": ["id", "name"], "limit": 2},
    )
    if len(exact) == 1:
        return exact[0], False
    fuzzy = execute(
        models, db, uid, api_key, "res.partner", "search_read",
        [[("name", "ilike", target)]],
        {"fields": ["id", "name"], "limit": 10},
    )
    matches = [row for row in fuzzy if norm(row["name"]) == norm(target)]
    if len(matches) == 1:
        return matches[0], False
    if not apply:
        return {"id": "", "name": target}, True
    partner_id = execute(models, db, uid, api_key, "res.partner", "create", [{"name": target, "customer_rank": 1}])
    return {"id": partner_id, "name": target}, True


def line_values(shop, accounts, tax):
    tax_ids = [(6, 0, [tax["id"]])] if money(shop["Tax"]) else [(6, 0, [])]
    no_tax_ids = [(6, 0, [])]
    lines = []
    service_total = money(shop["Labor"]) + money(shop["Fees"]) + money(shop["Sublet"])
    parts_total = money(shop["Parts"])
    if service_total:
        lines.append({
            "name": f"Shop Boss RO {shop['Shop Boss Number']} - labor/fees",
            "quantity": 1.0,
            "price_unit": float(service_total),
            "account_id": accounts["service"]["id"],
            "tax_ids": tax_ids,
        })
    if parts_total:
        lines.append({
            "name": f"Shop Boss RO {shop['Shop Boss Number']} - parts",
            "quantity": 1.0,
            "price_unit": float(parts_total),
            "account_id": accounts["parts"]["id"],
            "tax_ids": tax_ids,
        })
    if money(shop["Discount"]):
        lines.append({
            "name": f"Shop Boss RO {shop['Shop Boss Number']} - discount",
            "quantity": 1.0,
            "price_unit": -float(money(shop["Discount"])),
            "account_id": accounts["service"]["id"],
            "tax_ids": tax_ids,
        })
    return lines, no_tax_ids


def invoice_totals(models, db, uid, api_key, invoice_id):
    return execute(
        models, db, uid, api_key, "account.move", "read", [[invoice_id]],
        {"fields": ["id", "name", "state", "payment_state", "amount_untaxed", "amount_tax", "amount_total", "amount_residual", "ref", "invoice_origin", "invoice_line_ids"]},
    )[0]


def sync_tax_rounding(models, db, uid, api_key, invoice_id, shop, accounts, tax):
    invoice = invoice_totals(models, db, uid, api_key, invoice_id)
    tax_diff_cents = cents(shop["Tax"]) - cents(invoice["amount_tax"])
    if tax_diff_cents == 0:
        return
    if abs(tax_diff_cents) > 2:
        raise SystemExit(f"Invoice {invoice_id} tax difference is too large to auto-round: {tax_diff_cents} cents")
    sign = Decimal("1") if tax_diff_cents > 0 else Decimal("-1")
    adjustment_base = sign * Decimal("0.08")
    line_commands = [
        (0, 0, {
            "name": f"Shop Boss RO {shop['Shop Boss Number']} - tax rounding base",
            "quantity": 1.0,
            "price_unit": float(adjustment_base),
            "account_id": accounts["service"]["id"],
            "tax_ids": [(6, 0, [tax["id"]])],
        }),
        (0, 0, {
            "name": f"Shop Boss RO {shop['Shop Boss Number']} - tax rounding offset",
            "quantity": 1.0,
            "price_unit": float(-adjustment_base),
            "account_id": accounts["service"]["id"],
            "tax_ids": [(6, 0, [])],
        }),
    ]
    execute(models, db, uid, api_key, "account.move", "write", [[invoice_id], {"invoice_line_ids": line_commands}])


def prepare_invoice_lines(models, db, uid, api_key, invoice_id, shop, accounts, tax):
    lines, _ = line_values(shop, accounts, tax)
    execute(models, db, uid, api_key, "account.move", "write", [[invoice_id], {"invoice_line_ids": [(5, 0, 0), *[(0, 0, line) for line in lines]]}])
    sync_tax_rounding(models, db, uid, api_key, invoice_id, shop, accounts, tax)
    invoice = invoice_totals(models, db, uid, api_key, invoice_id)
    if cents(invoice["amount_untaxed"]) != cents(money(shop["Labor"]) + money(shop["Parts"]) + money(shop["Sublet"]) + money(shop["Fees"]) - money(shop["Discount"])):
        raise SystemExit(f"Invoice {invoice_id} untaxed total does not match Shop Boss RO {shop['Shop Boss Number']}")
    if cents(invoice["amount_tax"]) != cents(shop["Tax"]) or cents(invoice["amount_total"]) != cents(shop["Total"]):
        raise SystemExit(f"Invoice {invoice_id} total/tax does not match Shop Boss RO {shop['Shop Boss Number']}")


def add_result(results, status, action, shop, partner=None, invoice=None, reason=""):
    results.append({
        "Status": status,
        "Action": action,
        "Shop Boss RO": shop["Shop Boss Number"],
        "Customer": shop["Shop Boss Customer"],
        "Odoo Partner ID": (partner or {}).get("id", ""),
        "Odoo Partner": (partner or {}).get("name", ""),
        "Odoo Invoice ID": (invoice or {}).get("id", ""),
        "Odoo Invoice": (invoice or {}).get("name", ""),
        "Invoice Date": shop["Shop Boss Date ISO"],
        "Untaxed": money(shop["Labor"]) + money(shop["Parts"]) + money(shop["Sublet"]) + money(shop["Fees"]) - money(shop["Discount"]),
        "Tax": shop["Tax"],
        "Total": shop["Total"],
        "Reason": reason,
    })


def main():
    apply = "--apply" in os.sys.argv
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = single(execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY_NAME)]], {"fields": ["id", "name"], "limit": 2}), COMPANY_NAME)
    journal = single(execute(models, db, uid, api_key, "account.journal", "search_read", [[("name", "=", SALES_JOURNAL_NAME), ("company_id", "=", company["id"])]], {"fields": ["id", "name"], "limit": 2}), SALES_JOURNAL_NAME)
    tax = single(execute(models, db, uid, api_key, "account.tax", "search_read", [[("name", "=", SALES_TAX_NAME), ("company_id", "=", company["id"])]], {"fields": ["id", "name"], "limit": 2}), SALES_TAX_NAME)
    accounts = {
        "service": single(execute(models, db, uid, api_key, "account.account", "search_read", [[("name", "=", SERVICE_ACCOUNT_NAME), ("company_ids", "in", [company["id"]])]], {"fields": ["id", "name"], "limit": 2}), SERVICE_ACCOUNT_NAME),
        "parts": single(execute(models, db, uid, api_key, "account.account", "search_read", [[("name", "=", PARTS_ACCOUNT_NAME), ("company_ids", "in", [company["id"]])]], {"fields": ["id", "name"], "limit": 2}), PARTS_ACCOUNT_NAME),
    }

    shops = shop_rows_by_ro()
    results = []

    for ro in sorted(CREATE_ROS, key=int):
        shop = shops[ro]
        ref = f"Shop Boss RO {ro}"
        existing = execute(
            models, db, uid, api_key, "account.move", "search_read",
            [[("move_type", "=", "out_invoice"), ("state", "!=", "cancel"), "|", ("ref", "ilike", ref), ("invoice_origin", "ilike", ref)]],
            {"fields": ["id", "name"], "limit": 5},
        )
        if existing:
            add_result(results, "Skipped", "create_invoice", shop, invoice=existing[0], reason="Existing non-cancelled invoice already references this Shop Boss RO")
            continue
        partner, created = find_or_create_partner(models, db, uid, api_key, shop["Shop Boss Customer"], apply)
        if not apply:
            add_result(results, "Ready", "create_invoice", shop, partner=partner, reason="Would create partner and invoice" if created else "Would create invoice")
            continue
        invoice_id = execute(models, db, uid, api_key, "account.move", "create", [{
            "move_type": "out_invoice",
            "partner_id": partner["id"],
            "company_id": company["id"],
            "journal_id": journal["id"],
            "invoice_date": shop["Shop Boss Date ISO"],
            "invoice_date_due": shop["Shop Boss Date ISO"],
            "invoice_origin": ref,
            "ref": ref,
        }])
        prepare_invoice_lines(models, db, uid, api_key, invoice_id, shop, accounts, tax)
        execute_void_ok(models, db, uid, api_key, "account.move", "action_post", [[invoice_id]])
        invoice = invoice_totals(models, db, uid, api_key, invoice_id)
        add_result(results, "Posted", "create_invoice", shop, partner=partner, invoice=invoice, reason="Created partner and posted invoice" if created else "Posted invoice")

    for ro, invoice_id in UPDATE_EXISTING.items():
        shop = shops[ro]
        invoice = invoice_totals(models, db, uid, api_key, invoice_id)
        partner = {"id": "", "name": invoice.get("partner_id", ["", ""])[1] if isinstance(invoice.get("partner_id"), list) else ""}
        ref = invoice.get("ref") or ""
        origin = invoice.get("invoice_origin") or ""
        new_ref = ref if f"Shop Boss RO {ro}" in ref else (f"{ref}; Shop Boss RO {ro}" if ref else f"Shop Boss RO {ro}")
        new_origin = origin if f"Shop Boss RO {ro}" in origin else (f"{origin}; Shop Boss RO {ro}" if origin else f"Shop Boss RO {ro}")
        if not apply:
            add_result(results, "Ready", "update_existing", shop, partner=partner, invoice=invoice, reason=f"Would update existing invoice {invoice['name']}")
            continue
        if invoice["state"] != "draft":
            execute_void_ok(models, db, uid, api_key, "account.move", "button_draft", [[invoice_id]])
        if ro == "1107":
            prepare_invoice_lines(models, db, uid, api_key, invoice_id, shop, accounts, tax)
        execute(models, db, uid, api_key, "account.move", "write", [[invoice_id], {"ref": new_ref, "invoice_origin": new_origin}])
        execute_void_ok(models, db, uid, api_key, "account.move", "action_post", [[invoice_id]])
        invoice = invoice_totals(models, db, uid, api_key, invoice_id)
        add_result(results, "Updated", "update_existing", shop, partner=partner, invoice=invoice, reason=f"Updated/reposted existing invoice {invoice['name']}")

    write_csv(results)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
