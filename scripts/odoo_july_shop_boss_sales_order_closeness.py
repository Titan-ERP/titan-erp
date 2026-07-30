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
OUT = OUT_DIR / "july_shop_boss_sales_order_name_date_closeness.csv"
GROUP_OUT = OUT_DIR / "july_shop_boss_sales_order_grouped_closeness.csv"
SUMMARY = OUT_DIR / "july_shop_boss_sales_order_name_date_closeness.md"
COMPANY = "Southern Equipment Company (Laurel)"

STOP_WORDS = {
    "AND", "THE", "INC", "LLC", "CO", "CORP", "COMPANY", "CASH", "WALKINS",
    "WALKIN", "DBA", "OF", "MISSISSIPPI", "MS",
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
    hay = set(tokens(odoo_partner))
    best = 0
    for candidate in [shop_boss_customer, reverse_comma_name(shop_boss_customer)]:
        need = tokens(candidate)
        if not need:
            continue
        best = max(best, sum(1 for token in need if token in hay) / len(need))
    return best


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def july_shop_boss_docs():
    docs = []
    for row in read_csv(RO_FILE):
        day = parse_date(row.get("final_date"))
        if not day or day.month != 7 or day.year != 2026:
            continue
        amount = money(row.get("total_ro"))
        if not amount:
            amount = max([value for value in money_values(row.get("raw")) if value > 0] or [Decimal("0.00")])
        docs.append({
            "Shop Boss Type": "RO",
            "Shop Boss Number": row.get("ro_number", ""),
            "Shop Boss Date": row.get("final_date", ""),
            "Shop Boss Customer": row.get("customer", ""),
            "Shop Boss Amount": float(amount),
        })
    for row in read_csv(PS_FILE):
        day = parse_date(row.get("closed_date"))
        if not day or day.month != 7 or day.year != 2026:
            continue
        values = [value for value in money_values(row.get("raw")) if value > 0]
        amount = money(row.get("total_sale")) or (max(values) if values else Decimal("0.00"))
        docs.append({
            "Shop Boss Type": "PS",
            "Shop Boss Number": row.get("ps_number", ""),
            "Shop Boss Date": row.get("closed_date", ""),
            "Shop Boss Customer": row.get("customer", ""),
            "Shop Boss Amount": float(amount),
        })
    return [doc for doc in docs if money(doc["Shop Boss Amount"]) > 0]


def get_company(models, db, uid, api_key):
    rows = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})
    if not rows:
        raise SystemExit(f"Company not found: {COMPANY}")
    return rows[0]


def july_sales_orders(models, db, uid, api_key, company_id):
    return execute(
        models,
        db,
        uid,
        api_key,
        "sale.order",
        "search_read",
        [[
            ("company_id", "=", company_id),
            ("date_order", ">=", "2026-07-01"),
            ("date_order", "<", "2026-08-01"),
            ("state", "in", ["draft", "sent", "sale", "done"]),
        ]],
        {
            "fields": ["id", "name", "partner_id", "date_order", "state", "invoice_status", "amount_total", "invoice_ids", "origin", "client_order_ref"],
            "limit": 10000,
            "order": "date_order asc,id asc",
        },
    )


def rank_matches(orders, docs):
    rows = []
    for order in orders:
        order_day = parse_date(order.get("date_order"))
        partner = rel_name(order.get("partner_id"))
        order_amount = money(order.get("amount_total"))
        for doc in docs:
            score = token_score(doc["Shop Boss Customer"], partner)
            if score < 0.25:
                continue
            doc_day = parse_date(doc["Shop Boss Date"])
            gap = abs((order_day - doc_day).days) if order_day and doc_day else 9999
            if gap > 21 and score < 0.75:
                continue
            amount_diff = order_amount - money(doc["Shop Boss Amount"])
            name_points = score * 70
            date_points = max(0, 20 - min(gap, 20))
            amount_points = 10 if abs(amount_diff) <= Decimal("0.02") else max(0, 10 - min(float(abs(amount_diff)) / 50, 10))
            closeness = round(name_points + date_points + amount_points, 2)
            if closeness < 35:
                continue
            rows.append({
                "Closeness Score": closeness,
                "Name Score": round(score, 3),
                "Date Gap Days": gap,
                "Amount Difference": float(amount_diff),
                "Odoo Sale Order ID": order["id"],
                "Odoo Sale Order": order["name"],
                "Odoo Partner": partner,
                "Odoo Date": order.get("date_order", ""),
                "Odoo State": order.get("state", ""),
                "Odoo Invoice Status": order.get("invoice_status", ""),
                "Odoo Amount": float(order_amount),
                "Odoo Invoice Count": len(order.get("invoice_ids") or []),
                "Odoo Origin": order.get("origin", ""),
                "Shop Boss Type": doc["Shop Boss Type"],
                "Shop Boss Number": doc["Shop Boss Number"],
                "Shop Boss Date": doc["Shop Boss Date"],
                "Shop Boss Customer": doc["Shop Boss Customer"],
                "Shop Boss Amount": doc["Shop Boss Amount"],
                "Suggested Action": suggested_action(order, amount_diff, score, gap),
            })
    rows.sort(key=lambda row: (-row["Closeness Score"], row["Date Gap Days"], abs(Decimal(str(row["Amount Difference"])))))
    return rows


def grouped_matches(orders, docs):
    rows = []
    for order in orders:
        if order.get("invoice_ids"):
            continue
        partner = rel_name(order.get("partner_id"))
        order_day = parse_date(order.get("date_order"))
        order_amount = money(order.get("amount_total"))
        matches = []
        for doc in docs:
            score = token_score(doc["Shop Boss Customer"], partner)
            if score < 0.75:
                continue
            doc_day = parse_date(doc["Shop Boss Date"])
            gap = abs((order_day - doc_day).days) if order_day and doc_day else 9999
            if gap <= 14:
                matches.append((doc, score, gap))
        if not matches:
            continue
        total = sum(money(item[0]["Shop Boss Amount"]) for item in matches)
        diff = order_amount - total
        avg_score = sum(item[1] for item in matches) / len(matches)
        max_gap = max(item[2] for item in matches)
        if avg_score < 0.75:
            continue
        action = "Grouped amount match; review as combined Shop Boss docs."
        if abs(diff) <= Decimal("0.02"):
            action = "Grouped amount exactly matches; safe review candidate."
        elif abs(diff) > Decimal("250.00"):
            action = "Grouped customer/date match but amount differs materially."
        rows.append({
            "Odoo Sale Order ID": order["id"],
            "Odoo Sale Order": order["name"],
            "Odoo Partner": partner,
            "Odoo Date": order.get("date_order", ""),
            "Odoo State": order.get("state", ""),
            "Odoo Invoice Status": order.get("invoice_status", ""),
            "Odoo Amount": float(order_amount),
            "Matched Shop Boss Count": len(matches),
            "Matched Shop Boss Docs": "; ".join(f"{item[0]['Shop Boss Type']} {item[0]['Shop Boss Number']}" for item in matches),
            "Matched Shop Boss Dates": "; ".join(item[0]["Shop Boss Date"] for item in matches),
            "Matched Shop Boss Amount Total": float(total),
            "Amount Difference": float(diff),
            "Average Name Score": round(avg_score, 3),
            "Max Date Gap Days": max_gap,
            "Suggested Action": action,
        })
    rows.sort(key=lambda row: (abs(Decimal(str(row["Amount Difference"]))), -row["Average Name Score"], row["Max Date Gap Days"]))
    return rows


def suggested_action(order, amount_diff, score, gap):
    if order.get("invoice_ids"):
        return "Already has Odoo invoice attached; verify no duplicate action needed."
    if score >= 0.95 and gap <= 3 and abs(amount_diff) <= Decimal("0.02"):
        return "Exact closed Shop Boss match; safe invoice candidate."
    if score >= 0.75 and gap <= 7:
        return "Likely same customer/date; review amount before invoicing."
    return "Weak possible match; review manually only."


def write_summary(rows, grouped_rows, docs, orders):
    exact = [row for row in rows if row["Suggested Action"].startswith("Exact")]
    likely = [row for row in rows if row["Suggested Action"].startswith("Likely")]
    attached = [row for row in rows if row["Suggested Action"].startswith("Already")]
    lines = [
        "# July Shop Boss / Odoo Sales Order Closeness",
        "",
        "Focus: July 2026 only. Ranked by customer-name overlap, date proximity, and amount closeness.",
        "",
        f"- July Shop Boss closed docs reviewed: {len(docs)}",
        f"- July Odoo sales orders/quotations reviewed: {len(orders)}",
        f"- Candidate pairings found: {len(rows)}",
        f"- Exact safe invoice candidates without attached invoices: {len(exact)}",
        f"- Likely same customer/date but amount review needed: {len(likely)}",
        f"- Already attached to Odoo invoice: {len(attached)}",
        f"- Uninvoiced grouped customer/date candidates: {len(grouped_rows)}",
        "",
        "## Top Review Rows",
        "",
    ]
    for row in rows[:15]:
        lines.append(
            f"- Score {row['Closeness Score']}: {row['Odoo Sale Order']} {row['Odoo Partner']} `${row['Odoo Amount']}` vs "
            f"{row['Shop Boss Type']} {row['Shop Boss Number']} {row['Shop Boss Customer']} `${row['Shop Boss Amount']}` "
            f"({row['Date Gap Days']} days, diff `${row['Amount Difference']}`) - {row['Suggested Action']}"
        )
    lines.extend([
        "",
        "## Grouped Uninvoiced Candidates",
        "",
    ])
    if grouped_rows:
        for row in grouped_rows[:10]:
            lines.append(
                f"- {row['Odoo Sale Order']} {row['Odoo Partner']} `${row['Odoo Amount']}` vs "
                f"{row['Matched Shop Boss Count']} Shop Boss docs `{row['Matched Shop Boss Amount Total']}` "
                f"(diff `${row['Amount Difference']}`, max gap {row['Max Date Gap Days']} days): {row['Matched Shop Boss Docs']} - {row['Suggested Action']}"
            )
    else:
        lines.append("- None.")
    lines.extend([
        "",
        "File: `odoo_imports/accounting/july_shop_boss_sales_order_name_date_closeness.csv`",
        "Grouped file: `odoo_imports/accounting/july_shop_boss_sales_order_grouped_closeness.csv`",
    ])
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    db, uid, api_key, models = connect()
    company = get_company(models, db, uid, api_key)
    docs = july_shop_boss_docs()
    orders = july_sales_orders(models, db, uid, api_key, company["id"])
    rows = rank_matches(orders, docs)
    grouped_rows = grouped_matches(orders, docs)
    fields = [
        "Closeness Score", "Name Score", "Date Gap Days", "Amount Difference",
        "Odoo Sale Order ID", "Odoo Sale Order", "Odoo Partner", "Odoo Date",
        "Odoo State", "Odoo Invoice Status", "Odoo Amount", "Odoo Invoice Count", "Odoo Origin",
        "Shop Boss Type", "Shop Boss Number", "Shop Boss Date", "Shop Boss Customer", "Shop Boss Amount",
        "Suggested Action",
    ]
    write_csv(OUT, rows, fields)
    write_csv(
        GROUP_OUT,
        grouped_rows,
        [
            "Odoo Sale Order ID", "Odoo Sale Order", "Odoo Partner", "Odoo Date", "Odoo State",
            "Odoo Invoice Status", "Odoo Amount", "Matched Shop Boss Count", "Matched Shop Boss Docs",
            "Matched Shop Boss Dates", "Matched Shop Boss Amount Total", "Amount Difference",
            "Average Name Score", "Max Date Gap Days", "Suggested Action",
        ],
    )
    write_summary(rows, grouped_rows, docs, orders)
    print(f"Connected uid: {uid}")
    print(f"July Shop Boss closed docs: {len(docs)}")
    print(f"July Odoo sales orders reviewed: {len(orders)}")
    print(f"Candidate pairings: {len(rows)}")
    print(f"Grouped candidates: {len(grouped_rows)}")
    print(f"Output: {OUT}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    main()
