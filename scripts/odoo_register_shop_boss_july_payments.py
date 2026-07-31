import csv
import os
import re
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
MATCH_AUDIT = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_odoo_all_invoice_match_audit_2026_07.csv"
PAYMENTS = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_payments_received_2026_07.csv"
PART_SALES = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_part_sales_production_detail_2026_07.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_shop_boss_july_payment_registration_results.csv"

BANK_JOURNAL_NAME = "Bank"
CASH_JOURNAL_NAME = "Cash"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(rows):
    fields = [
        "Status", "Action", "Shop Boss Type", "Shop Boss Number", "Payment Date", "Payment Type",
        "Payment Amount", "Applied Amount", "Odoo Invoice ID", "Odoo Invoice", "Before Residual",
        "After Residual", "Odoo Payment State", "Journal", "Payment ID", "Payment Name", "Reason",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def iso_from_mmddyyyy(value):
    month, day, year = str(value).split("/")
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def payment_key(shop_type, number):
    return ("repair_order_payment" if shop_type == "RO" else "part_sale_payment", number)


def journal_for(payment_type, journals):
    if re.search(r"\bcash\b", str(payment_type or ""), re.I):
        return journals["cash"]
    return journals["bank"]


def first_inbound_method_line(models, db, uid, api_key, journal_id):
    rows = execute(
        models, db, uid, api_key, "account.payment.method.line", "search_read",
        [[("journal_id", "=", journal_id), ("payment_type", "=", "inbound")]],
        {"fields": ["id", "name"], "limit": 10},
    )
    if rows:
        return rows[0]["id"]
    rows = execute(
        models, db, uid, api_key, "account.payment.method.line", "search_read",
        [[("journal_id", "=", journal_id)]],
        {"fields": ["id", "name"], "limit": 1},
    )
    if not rows:
        raise SystemExit(f"No payment method line found for journal {journal_id}")
    return rows[0]["id"]


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

    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", "Southern Equipment Company (Laurel)")]], {"fields": ["id"], "limit": 1})[0]
    bank = execute(models, db, uid, api_key, "account.journal", "search_read", [[("name", "=", BANK_JOURNAL_NAME), ("company_id", "=", company["id"])]], {"fields": ["id", "name"], "limit": 1})[0]
    cash = execute(models, db, uid, api_key, "account.journal", "search_read", [[("name", "=", CASH_JOURNAL_NAME), ("company_id", "=", company["id"])]], {"fields": ["id", "name"], "limit": 1})[0]
    journals = {
        "bank": {"id": bank["id"], "name": bank["name"], "method_line_id": first_inbound_method_line(models, db, uid, api_key, bank["id"])},
        "cash": {"id": cash["id"], "name": cash["name"], "method_line_id": first_inbound_method_line(models, db, uid, api_key, cash["id"])},
    }

    payment_rows = defaultdict(list)
    seen = set()
    for row in read_csv(PAYMENTS):
        dedupe_key = (row["type"], row["number"], row["payment_date"], row["payment_type"].lower(), str(money(row["amount"])))
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        payment_rows[(row["type"], row["number"])].append(row)
    for rows in payment_rows.values():
        rows.sort(key=lambda row: (iso_from_mmddyyyy(row["payment_date"]), money(row["amount"])))

    for row in read_csv(PART_SALES):
        amount = money(row.get("Payments"))
        if amount <= 0:
            continue
        key = ("part_sale_payment", row["Shop Boss PS"])
        if key in payment_rows:
            continue
        payment_rows[key].append(
            {
                "type": "part_sale_payment",
                "number": row["Shop Boss PS"],
                "payment_date": row["Closed Date"],
                "payment_type": row.get("Payment Source") or "Shop Boss payment",
                "amount": str(amount),
            }
        )

    audit_rows = [row for row in read_csv(MATCH_AUDIT) if row["Status"] == "Confirmed"]
    invoice_ids = [int(row["Odoo Invoice ID"]) for row in audit_rows if row["Odoo Invoice ID"]]
    invoices = execute(
        models, db, uid, api_key, "account.move", "read", [invoice_ids],
        {"fields": ["id", "name", "state", "payment_state", "amount_residual", "amount_total"]},
    )
    invoice_by_id = {str(row["id"]): row for row in invoices}
    results = []

    for audit in audit_rows:
        inv = invoice_by_id.get(audit["Odoo Invoice ID"])
        if not inv or inv["state"] != "posted":
            continue
        residual = money(inv["amount_residual"])
        if residual <= 0:
            continue
        rows = payment_rows.get(payment_key(audit["Shop Boss Type"], audit["Shop Boss Number"]), [])
        if not rows:
            continue
        for payment in rows:
            if residual <= 0:
                break
            amount = money(payment["amount"])
            if amount <= 0:
                continue
            applied = min(amount, residual)
            journal = journal_for(payment["payment_type"], journals)
            base = {
                "Shop Boss Type": audit["Shop Boss Type"],
                "Shop Boss Number": audit["Shop Boss Number"],
                "Payment Date": iso_from_mmddyyyy(payment["payment_date"]),
                "Payment Type": payment["payment_type"],
                "Payment Amount": amount,
                "Applied Amount": applied,
                "Odoo Invoice ID": inv["id"],
                "Odoo Invoice": inv["name"],
                "Before Residual": residual,
                "Journal": journal["name"],
            }
            if not apply:
                results.append({**base, "Status": "Ready", "Action": "register_payment", "Reason": "Would register Shop Boss payment up to current residual."})
                residual -= applied
                continue

            context = {"active_model": "account.move", "active_ids": [inv["id"]], "active_id": inv["id"]}
            wizard_id = execute(
                models, db, uid, api_key, "account.payment.register", "create",
                [{
                    "amount": float(applied),
                    "payment_date": iso_from_mmddyyyy(payment["payment_date"]),
                    "journal_id": journal["id"],
                    "payment_method_line_id": journal["method_line_id"],
                    "communication": f"Shop Boss {audit['Shop Boss Type']} {audit['Shop Boss Number']} {payment['payment_type']}",
                }],
                {"context": context},
            )
            action = execute(models, db, uid, api_key, "account.payment.register", "action_create_payments", [[wizard_id]], {"context": context})
            inv_after = execute(
                models, db, uid, api_key, "account.move", "read", [[inv["id"]]],
                {"fields": ["amount_residual", "payment_state"]},
            )[0]
            payment_id = ""
            payment_name = ""
            if isinstance(action, dict):
                payment_id = action.get("res_id") or ""
                if payment_id:
                    payment_rec = execute(models, db, uid, api_key, "account.payment", "read", [[payment_id]], {"fields": ["id", "name"]})[0]
                    payment_name = payment_rec.get("name") or ""
            results.append({
                **base,
                "Status": "Registered",
                "Action": "register_payment",
                "After Residual": inv_after["amount_residual"],
                "Odoo Payment State": inv_after["payment_state"],
                "Payment ID": payment_id,
                "Payment Name": payment_name,
                "Reason": "Registered Shop Boss payment.",
            })
            residual = money(inv_after["amount_residual"])

    write_csv(results)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
