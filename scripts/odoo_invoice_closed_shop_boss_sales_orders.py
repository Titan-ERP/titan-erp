import argparse
import csv
import os
import re
import xmlrpc.client
from datetime import datetime
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_BOSS = ROOT / "odoo_imports" / "shop_boss"
OUT_DIR = ROOT / "odoo_imports" / "accounting"

RO_FILE = SHOP_BOSS / "shop_boss_finalized_ro_rows_ytd_2026.csv"
PS_FILE = SHOP_BOSS / "shop_boss_part_sale_rows_ytd_2026.csv"
PLAN = OUT_DIR / "closed_shop_boss_sales_order_invoice_plan_2026.csv"
RESULTS = OUT_DIR / "closed_shop_boss_sales_order_invoice_results_2026.csv"
SUMMARY = OUT_DIR / "closed_shop_boss_sales_order_invoice_summary_2026.md"
REVIEW = OUT_DIR / "closed_shop_boss_sales_order_invoice_review_candidates_2026.csv"

COMPANY = "Southern Equipment Company (Laurel)"
STOP_WORDS = {"AND", "THE", "INC", "LLC", "CO", "CORP", "COMPANY", "CASH", "WALKINS", "WALKIN"}


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def money(value):
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return Decimal("0.00")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def money_values(text):
    # Avoid treating years/check numbers such as 2026 as money. Bare values must
    # include cents; whole-dollar values must carry a dollar sign.
    return [money(value) for value in re.findall(r"\$-?\d[\d,]*(?:\.\d{2})?|-?\d[\d,]*\.\d{2}", str(text or ""))]


def parse_date(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19], fmt).date()
        except ValueError:
            pass
    return None


def normalize(text):
    value = str(text or "").upper().replace("&", " AND ")
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def reverse_comma_name(name):
    text = str(name or "").strip()
    if "," not in text:
        return text
    left, right = [part.strip() for part in text.split(",", 1)]
    return f"{right} {left}".strip()


def tokens(text):
    return [part for part in normalize(text).split() if len(part) >= 2 and part not in STOP_WORDS]


def token_score(shop_boss_customer, odoo_partner):
    choices = [shop_boss_customer, reverse_comma_name(shop_boss_customer)]
    best = 0
    hay = set(tokens(odoo_partner))
    for choice in choices:
        need = tokens(choice)
        if not need:
            continue
        best = max(best, sum(1 for token in need if token in hay) / len(need))
    return best


def shop_boss_docs():
    docs = []
    for row in read_csv(RO_FILE):
        amount = money(row.get("total_ro"))
        if not amount:
            amount = max([value for value in money_values(row.get("raw")) if value > 0] or [Decimal("0.00")])
        if amount <= 0:
            continue
        docs.append({
            "Shop Boss Type": "RO",
            "Shop Boss Number": row.get("ro_number", ""),
            "Shop Boss Date": row.get("final_date", ""),
            "Shop Boss Customer": row.get("customer", ""),
            "Shop Boss Amount": float(amount),
        })
    for row in read_csv(PS_FILE):
        values = [value for value in money_values(row.get("raw")) if value > 0]
        amount = money(row.get("total_sale")) or (max(values) if values else Decimal("0.00"))
        if amount <= 0:
            continue
        docs.append({
            "Shop Boss Type": "PS",
            "Shop Boss Number": row.get("ps_number", ""),
            "Shop Boss Date": row.get("closed_date", ""),
            "Shop Boss Customer": row.get("customer", ""),
            "Shop Boss Amount": float(amount),
        })
    return docs


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def get_company(models, db, uid, api_key):
    rows = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})
    if not rows:
        raise SystemExit(f"Company not found: {COMPANY}")
    return rows[0]


def sale_orders(models, db, uid, api_key, company_id):
    return execute(
        models,
        db,
        uid,
        api_key,
        "sale.order",
        "search_read",
        [[
            ("company_id", "=", company_id),
            ("state", "in", ["draft", "sent", "sale", "done"]),
        ]],
        {
            "fields": [
                "id", "name", "partner_id", "date_order", "state", "invoice_status",
                "amount_total", "invoice_ids", "client_order_ref", "origin",
            ],
            "limit": 10000,
            "order": "date_order asc,id asc",
        },
    )


def existing_invoice_match(models, db, uid, api_key, partner_id, amount, date_value):
    if not partner_id:
        return ""
    date_text = date_value.isoformat() if date_value else False
    domain = [
        ("move_type", "=", "out_invoice"),
        ("partner_id", "=", partner_id),
        ("amount_total", ">=", float(amount - Decimal("0.02"))),
        ("amount_total", "<=", float(amount + Decimal("0.02"))),
        ("state", "!=", "cancel"),
    ]
    if date_text:
        domain.append(("invoice_date", ">=", date_text))
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "account.move",
        "search_read",
        [domain],
        {"fields": ["id", "name", "state", "invoice_date", "amount_total"], "limit": 5, "order": "invoice_date asc,id asc"},
    )
    return "; ".join(f"{row['name']} {row['state']} {row.get('invoice_date')} ${row.get('amount_total')}" for row in rows)


def build_plan(models, db, uid, api_key, orders, docs):
    rows = []
    used_docs = set()
    for order in orders:
        partner_name = rel_name(order.get("partner_id"))
        order_amount = money(order.get("amount_total"))
        if order_amount <= 0:
            continue
        order_date = parse_date(order.get("date_order"))
        candidates = []
        for doc in docs:
            doc_key = (doc["Shop Boss Type"], doc["Shop Boss Number"])
            doc_amount = money(doc["Shop Boss Amount"])
            if abs(order_amount - doc_amount) > Decimal("0.02"):
                continue
            score = token_score(doc["Shop Boss Customer"], partner_name)
            if score < 0.75:
                continue
            doc_date = parse_date(doc["Shop Boss Date"])
            gap = abs((order_date - doc_date).days) if order_date and doc_date else 9999
            candidates.append((score, gap, doc_key, doc))
        candidates.sort(key=lambda item: (-item[0], item[1], item[2]))
        if not candidates:
            continue
        score, gap, doc_key, doc = candidates[0]
        attached_invoice_count = len(order.get("invoice_ids") or [])
        partner_id = order["partner_id"][0] if isinstance(order.get("partner_id"), list) else False
        existing = existing_invoice_match(models, db, uid, api_key, partner_id, order_amount, parse_date(doc["Shop Boss Date"]))
        status = "Ready"
        reason = "Strong Shop Boss closed/finalized match."
        if doc_key in used_docs:
            status = "Review"
            reason = "Shop Boss document already matched another Odoo sales order in this plan."
        elif attached_invoice_count:
            status = "Skipped"
            reason = "Sales order already has invoice_ids attached."
        elif existing:
            status = "Skipped"
            reason = "Existing non-cancelled Odoo invoice already matches partner/amount/date."
        elif order.get("state") in {"sale", "done"} and order.get("invoice_status") not in {"to invoice"}:
            status = "Review"
            reason = f"Confirmed sales order invoice_status is {order.get('invoice_status')}, not to invoice."
        if status == "Ready":
            used_docs.add(doc_key)
        rows.append({
            "Status": status,
            "Reason": reason,
            "Odoo Sale Order ID": order["id"],
            "Odoo Sale Order": order["name"],
            "Odoo Partner": partner_name,
            "Odoo Date": order.get("date_order", ""),
            "Odoo State": order.get("state", ""),
            "Odoo Invoice Status": order.get("invoice_status", ""),
            "Odoo Amount": float(order_amount),
            "Attached Invoice Count": attached_invoice_count,
            "Existing Matching Invoice": existing,
            "Shop Boss Type": doc["Shop Boss Type"],
            "Shop Boss Number": doc["Shop Boss Number"],
            "Shop Boss Date": doc["Shop Boss Date"],
            "Shop Boss Customer": doc["Shop Boss Customer"],
            "Shop Boss Amount": doc["Shop Boss Amount"],
            "Match Score": round(score, 3),
            "Date Gap Days": gap,
        })
    return rows


def build_review_candidates(orders, docs):
    rows = []
    for order in orders:
        if order.get("invoice_ids"):
            continue
        partner_name = rel_name(order.get("partner_id"))
        order_amount = money(order.get("amount_total"))
        order_date = parse_date(order.get("date_order"))
        if order_amount <= 0:
            continue
        candidates = []
        for doc in docs:
            score = token_score(doc["Shop Boss Customer"], partner_name)
            if score < 0.75:
                continue
            doc_date = parse_date(doc["Shop Boss Date"])
            gap = abs((order_date - doc_date).days) if order_date and doc_date else 9999
            if gap > 45:
                continue
            amount_diff = order_amount - money(doc["Shop Boss Amount"])
            candidates.append((gap, abs(amount_diff), score, amount_diff, doc))
        candidates.sort(key=lambda item: (item[0], item[1], -item[2]))
        for gap, _abs_diff, score, amount_diff, doc in candidates[:3]:
            rows.append({
                "Review Status": "Possible closed Shop Boss match - amount differs",
                "Odoo Sale Order ID": order["id"],
                "Odoo Sale Order": order["name"],
                "Odoo Partner": partner_name,
                "Odoo Date": order.get("date_order", ""),
                "Odoo State": order.get("state", ""),
                "Odoo Invoice Status": order.get("invoice_status", ""),
                "Odoo Amount": float(order_amount),
                "Shop Boss Type": doc["Shop Boss Type"],
                "Shop Boss Number": doc["Shop Boss Number"],
                "Shop Boss Date": doc["Shop Boss Date"],
                "Shop Boss Customer": doc["Shop Boss Customer"],
                "Shop Boss Amount": doc["Shop Boss Amount"],
                "Amount Difference": float(amount_diff),
                "Match Score": round(score, 3),
                "Date Gap Days": gap,
            })
    return rows


def create_invoice_for_order(models, db, uid, api_key, order_id):
    wizard_id = execute(
        models,
        db,
        uid,
        api_key,
        "sale.advance.payment.inv",
        "create",
        [{"advance_payment_method": "delivered", "sale_order_ids": [(6, 0, [order_id])]}],
        {"context": {"active_model": "sale.order", "active_ids": [order_id], "active_id": order_id}},
    )
    execute(
        models,
        db,
        uid,
        api_key,
        "sale.advance.payment.inv",
        "create_invoices",
        [[wizard_id]],
        {"context": {"active_model": "sale.order", "active_ids": [order_id], "active_id": order_id}},
    )


def apply_ready(models, db, uid, api_key, plan_rows, post):
    results = []
    for row in plan_rows:
        if row["Status"] != "Ready":
            results.append({**row, "Apply Status": "Not Applied", "Created Invoices": "", "Posted Invoices": "", "Apply Message": row["Reason"]})
            continue
        order_id = int(row["Odoo Sale Order ID"])
        try:
            before = execute(models, db, uid, api_key, "sale.order", "read", [[order_id]], {"fields": ["state", "invoice_ids", "invoice_status"]})[0]
            if before["state"] in {"draft", "sent"}:
                execute(models, db, uid, api_key, "sale.order", "action_confirm", [[order_id]])
            create_invoice_for_order(models, db, uid, api_key, order_id)
            after = execute(models, db, uid, api_key, "sale.order", "read", [[order_id]], {"fields": ["invoice_ids", "invoice_status"]})[0]
            new_invoice_ids = [invoice_id for invoice_id in after.get("invoice_ids", []) if invoice_id not in before.get("invoice_ids", [])]
            posted = []
            if post and new_invoice_ids:
                execute(models, db, uid, api_key, "account.move", "action_post", [new_invoice_ids])
                posted = new_invoice_ids
            invoices = execute(
                models,
                db,
                uid,
                api_key,
                "account.move",
                "read",
                [new_invoice_ids],
                {"fields": ["name", "state", "amount_total"]},
            ) if new_invoice_ids else []
            results.append({
                **row,
                "Apply Status": "Applied" if new_invoice_ids else "Review",
                "Created Invoices": "; ".join(f"{invoice['name']} {invoice['state']} ${invoice['amount_total']}" for invoice in invoices),
                "Posted Invoices": "; ".join(map(str, posted)),
                "Apply Message": "Created invoice from matched Shop Boss closed sales order." if new_invoice_ids else "Odoo created no invoice; check delivered/invoicing policy.",
            })
        except Exception as exc:
            results.append({**row, "Apply Status": "Error", "Created Invoices": "", "Posted Invoices": "", "Apply Message": str(exc)})
    return results


def write_summary(plan_rows, result_rows, applied):
    ready = sum(1 for row in plan_rows if row["Status"] == "Ready")
    skipped = sum(1 for row in plan_rows if row["Status"] == "Skipped")
    review = sum(1 for row in plan_rows if row["Status"] == "Review")
    applied_count = sum(1 for row in result_rows if row.get("Apply Status") == "Applied")
    lines = [
        "# Closed Shop Boss Sales Order Invoice Conversion",
        "",
        f"Odoo write performed: {'yes' if applied else 'no, dry run only'}",
        "",
        f"- Strong Shop Boss/Odoo sales order matches found: {len(plan_rows)}",
        f"- Ready to invoice: {ready}",
        f"- Skipped because invoice already exists/attached: {skipped}",
        f"- Needs review: {review}",
        f"- Applied invoice creations: {applied_count}",
        f"- Review-only possible closed matches: {len(read_csv(REVIEW)) if REVIEW.exists() else 0}",
        "",
        "## Files",
        "",
        "- Plan CSV: `odoo_imports/accounting/closed_shop_boss_sales_order_invoice_plan_2026.csv`",
        "- Results CSV: `odoo_imports/accounting/closed_shop_boss_sales_order_invoice_results_2026.csv`",
        "- Review candidates CSV: `odoo_imports/accounting/closed_shop_boss_sales_order_invoice_review_candidates_2026.csv`",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Invoice Odoo sales orders/quotations when strongly matched to closed Shop Boss documents.")
    parser.add_argument("--apply", action="store_true", help="Confirm ready quotations and create invoices. Default is dry run.")
    parser.add_argument("--post", action="store_true", help="Post newly created invoices after creation. Default leaves invoices as draft.")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    company = get_company(models, db, uid, api_key)
    docs = shop_boss_docs()
    orders = sale_orders(models, db, uid, api_key, company["id"])
    plan_rows = build_plan(models, db, uid, api_key, orders, docs)
    review_rows = build_review_candidates(orders, docs)
    fields = [
        "Status", "Reason", "Odoo Sale Order ID", "Odoo Sale Order", "Odoo Partner", "Odoo Date",
        "Odoo State", "Odoo Invoice Status", "Odoo Amount", "Attached Invoice Count",
        "Existing Matching Invoice", "Shop Boss Type", "Shop Boss Number", "Shop Boss Date",
        "Shop Boss Customer", "Shop Boss Amount", "Match Score", "Date Gap Days",
    ]
    write_csv(PLAN, plan_rows, fields)
    write_csv(
        REVIEW,
        review_rows,
        [
            "Review Status", "Odoo Sale Order ID", "Odoo Sale Order", "Odoo Partner", "Odoo Date",
            "Odoo State", "Odoo Invoice Status", "Odoo Amount", "Shop Boss Type", "Shop Boss Number",
            "Shop Boss Date", "Shop Boss Customer", "Shop Boss Amount", "Amount Difference",
            "Match Score", "Date Gap Days",
        ],
    )
    result_rows = apply_ready(models, db, uid, api_key, plan_rows, args.post) if args.apply else [
        {**row, "Apply Status": "Ready" if row["Status"] == "Ready" else "Not Applied", "Created Invoices": "", "Posted Invoices": "", "Apply Message": row["Reason"]}
        for row in plan_rows
    ]
    write_csv(RESULTS, result_rows, fields + ["Apply Status", "Created Invoices", "Posted Invoices", "Apply Message"])
    write_summary(plan_rows, result_rows, args.apply)
    print(f"Connected uid: {uid}")
    print(f"Shop Boss closed/finalized docs: {len(docs)}")
    print(f"Odoo sales orders reviewed: {len(orders)}")
    print(f"Strong matches: {len(plan_rows)}")
    print(f"Ready: {sum(1 for row in plan_rows if row['Status'] == 'Ready')}")
    print(f"Review candidates: {len(review_rows)}")
    print(f"Applied: {sum(1 for row in result_rows if row.get('Apply Status') == 'Applied')}")
    print(f"Plan: {PLAN}")
    print(f"Results: {RESULTS}")


if __name__ == "__main__":
    main()
