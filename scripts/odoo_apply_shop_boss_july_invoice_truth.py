import csv
import os
import re
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_PART_SALES = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_part_sales_production_detail_2026_07.csv"
AUDIT = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_odoo_existing_invoice_match_audit_2026_07.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_shop_boss_july_invoice_truth_apply_results.csv"

COMPANY_NAME = "Southern Equipment Company (Laurel)"
SALES_JOURNAL_NAME = "Sales"
PARTS_INCOME_ACCOUNT_NAME = "Parts Revenue"
SALES_TAX_NAME = "MS Sales Tax - 7%"

SHOP_BOSS_CORRECT_ACTIONS = {
    "No Odoo match": "create_invoice",
    "Review": "create_invoice",
}

PARTNER_OVERRIDES = {
    "CASH": "cash",
    "WALKINS CASH": "cash",
    "GREG GRIFFIN": "Greg Griffin",
    "MICHAEL MCORMICK": "Michael McCormick",
    "TIM GRAVES": "Tim Graves",
    "ROBERT PALMER": "Robert Palmer",
    "MIKE BATTON CONSTRUCTION": "Mike Batton Construction",
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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "Status",
        "Action",
        "Shop Boss PS",
        "Shop Boss Customer",
        "Odoo Partner ID",
        "Odoo Partner",
        "Odoo Invoice ID",
        "Odoo Invoice",
        "Invoice Date",
        "Parts",
        "Tax",
        "Total",
        "Reason",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def norm(value):
    raw = str(value or "").upper().replace("&", " AND ").strip()
    if "," in raw:
        left, right = [part.strip() for part in raw.split(",", 1)]
        if left and right:
            raw = f"{right} {left}"
    text = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def source_by_ps():
    return {row["Shop Boss PS"]: row for row in read_csv(SHOP_PART_SALES)}


def get_partner_name(shop_customer):
    normalized = norm(shop_customer)
    return PARTNER_OVERRIDES.get(normalized, " ".join(part.title() for part in normalized.split()))


def find_or_create_partner(models, db, uid, api_key, shop_customer, apply):
    target_name = get_partner_name(shop_customer)
    exact = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "search_read",
        [[("name", "=", target_name)]],
        {"fields": ["id", "name"], "limit": 2},
    )
    if len(exact) == 1:
        return exact[0], False

    fuzzy = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "search_read",
        [[("name", "ilike", target_name)]],
        {"fields": ["id", "name"], "limit": 10},
    )
    matches = [row for row in fuzzy if norm(row["name"]) == norm(target_name)]
    if len(matches) == 1:
        return matches[0], False

    if not apply:
        return {"id": "", "name": target_name}, True

    partner_id = execute(
        models,
        db,
        uid,
        api_key,
        "res.partner",
        "create",
        [{"name": target_name, "customer_rank": 1}],
    )
    return {"id": partner_id, "name": target_name}, True


def single(rows, label):
    if len(rows) != 1:
        raise SystemExit(f"Expected exactly one {label}; found {len(rows)}")
    return rows[0]


def add_result(rows, status, action, shop, partner=None, invoice=None, reason=""):
    rows.append(
        {
            "Status": status,
            "Action": action,
            "Shop Boss PS": shop["Shop Boss PS"],
            "Shop Boss Customer": shop["Shop Boss Customer"],
            "Odoo Partner ID": (partner or {}).get("id", ""),
            "Odoo Partner": (partner or {}).get("name", ""),
            "Odoo Invoice ID": (invoice or {}).get("id", ""),
            "Odoo Invoice": (invoice or {}).get("name", ""),
            "Invoice Date": shop["Closed Date ISO"],
            "Parts": shop["Parts"],
            "Tax": shop["Tax"],
            "Total": shop["Total Sale"],
            "Reason": reason,
        }
    )


def create_shop_boss_invoice(models, db, uid, api_key, apply, company, journal, income, tax, shop):
    ref = f"Shop Boss PS {shop['Shop Boss PS']}"
    existing = execute(
        models,
        db,
        uid,
        api_key,
        "account.move",
        "search_read",
        [[("move_type", "=", "out_invoice"), ("state", "!=", "cancel"), "|", ("ref", "ilike", ref), ("invoice_origin", "ilike", ref)]],
        {"fields": ["id", "name", "state", "payment_state"], "limit": 5},
    )
    if existing:
        return "Skipped", None, existing[0], "Existing non-cancelled invoice already references this Shop Boss PS"

    partner, created_partner = find_or_create_partner(models, db, uid, api_key, shop["Shop Boss Customer"], apply)
    line_values = {
        "name": ref,
        "quantity": 1.0,
        "price_unit": float(money(shop["Parts"]) + money(shop["Fees"])),
        "account_id": income["id"],
        "tax_ids": [(6, 0, [tax["id"]])] if money(shop["Tax"]) else [(6, 0, [])],
    }
    invoice_values = {
        "move_type": "out_invoice",
        "partner_id": partner["id"],
        "company_id": company["id"],
        "journal_id": journal["id"],
        "invoice_date": shop["Closed Date ISO"],
        "invoice_date_due": shop["Closed Date ISO"],
        "invoice_origin": ref,
        "ref": ref,
        "invoice_line_ids": [(0, 0, line_values)],
    }
    if not apply:
        reason = "Would create partner and invoice" if created_partner else "Would create invoice"
        return "Ready", partner, {}, reason

    invoice_id = execute(models, db, uid, api_key, "account.move", "create", [invoice_values])
    execute(models, db, uid, api_key, "account.move", "action_post", [[invoice_id]])
    invoice = execute(
        models,
        db,
        uid,
        api_key,
        "account.move",
        "read",
        [[invoice_id]],
        {"fields": ["id", "name", "payment_state"]},
    )[0]
    reason = "Created partner and posted invoice" if created_partner else "Posted invoice"
    return "Posted", partner, invoice, reason


def fix_zach_freeman_tax(models, db, uid, api_key, apply, tax, shop):
    invoice_id = 639
    invoice = execute(
        models,
        db,
        uid,
        api_key,
        "account.move",
        "read",
        [[invoice_id]],
        {"fields": ["id", "name", "state", "payment_state", "amount_untaxed", "amount_tax", "amount_total", "invoice_line_ids", "ref"]},
    )[0]
    if money(invoice["amount_untaxed"]) != money(shop["Parts"]) or money(invoice["amount_tax"]) != Decimal("0.00"):
        return "Review", {"id": 311, "name": "Zach Freeman"}, invoice, "Existing invoice no longer has the expected untaxed/no-tax state"
    if not apply:
        return "Ready", {"id": 311, "name": "Zach Freeman"}, invoice, "Would reset to draft, apply sales tax, and repost"

    if invoice["state"] != "draft":
        execute_void_ok(models, db, uid, api_key, "account.move", "button_draft", [[invoice_id]])
    execute(models, db, uid, api_key, "account.move.line", "write", [invoice["invoice_line_ids"], {"tax_ids": [(6, 0, [tax["id"]])]}])
    new_ref = invoice.get("ref") or ""
    if "Shop Boss PS 412" not in new_ref:
        new_ref = f"{new_ref}; Shop Boss PS 412" if new_ref else "Shop Boss PS 412"
    execute(models, db, uid, api_key, "account.move", "write", [[invoice_id], {"ref": new_ref}])
    execute_void_ok(models, db, uid, api_key, "account.move", "action_post", [[invoice_id]])
    updated = execute(
        models,
        db,
        uid,
        api_key,
        "account.move",
        "read",
        [[invoice_id]],
        {"fields": ["id", "name", "payment_state", "amount_tax", "amount_total"]},
    )[0]
    return "Updated", {"id": 311, "name": "Zach Freeman"}, updated, "Applied Shop Boss 7% sales tax and reposted"


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
    income = single(execute(models, db, uid, api_key, "account.account", "search_read", [[("name", "=", PARTS_INCOME_ACCOUNT_NAME), ("company_ids", "in", [company["id"]])]], {"fields": ["id", "code", "name"], "limit": 2}), PARTS_INCOME_ACCOUNT_NAME)
    tax = single(execute(models, db, uid, api_key, "account.tax", "search_read", [[("name", "=", SALES_TAX_NAME), ("company_id", "=", company["id"])]], {"fields": ["id", "name"], "limit": 2}), SALES_TAX_NAME)

    shops = source_by_ps()
    audit_rows = read_csv(AUDIT)
    results = []

    for row in audit_rows:
        ps = row["Shop Boss PS"]
        shop = shops[ps]
        if ps == "412":
            status, partner, invoice, reason = fix_zach_freeman_tax(models, db, uid, api_key, apply, tax, shop)
            add_result(results, status, "fix_existing_tax", shop, partner, invoice, reason)
            continue
        if row["Status"] not in SHOP_BOSS_CORRECT_ACTIONS:
            continue
        status, partner, invoice, reason = create_shop_boss_invoice(models, db, uid, api_key, apply, company, journal, income, tax, shop)
        add_result(results, status, SHOP_BOSS_CORRECT_ACTIONS[row["Status"]], shop, partner, invoice, reason)

    write_csv(results)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
