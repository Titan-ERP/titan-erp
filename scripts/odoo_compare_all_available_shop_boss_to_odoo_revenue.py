import csv
import os
import re
import sys
import xmlrpc.client
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_BOSS = ROOT / "odoo_imports" / "shop_boss"
OUT_DIR = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26"
RO_FILE = SHOP_BOSS / "shop_boss_finalized_ro_rows_ytd_2026.csv"
PS_FILE = SHOP_BOSS / "shop_boss_part_sale_rows_ytd_2026.csv"
DETAIL = OUT_DIR / "all_available_shop_boss_vs_odoo_revenue_by_month.csv"
SUMMARY = OUT_DIR / "all_available_shop_boss_vs_odoo_revenue_summary.md"
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


def money(value):
    text = str(value or "").replace("$", "").replace(",", "").replace("\x02", "").replace("\x03", "").replace("\x08", "").strip()
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
            continue
    return None


def month_key(value):
    day = parse_date(value)
    return day.strftime("%Y-%m") if day else "unknown"


def read_shop_boss():
    rows = []
    with RO_FILE.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            total = money(row.get("total_ro"))
            if not total:
                values = [value for value in money_values(row.get("raw")) if value > 0]
                total = max(values or [Decimal("0.00")])
            if total <= 0:
                continue
            service = money(row.get("labor")) + money(row.get("sublet")) + money(row.get("fees")) - money(row.get("discount"))
            parts = money(row.get("parts"))
            tax = money(row.get("tax"))
            rows.append({"month": month_key(row.get("final_date")), "type": "RO", "service": service, "parts": parts, "tax": tax, "total": total})
    with PS_FILE.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            total = money(row.get("total_sale"))
            if not total:
                values = [value for value in money_values(row.get("raw")) if value > 0]
                total = max(values or [Decimal("0.00")])
            if total <= 0:
                continue
            raw_values = [value for value in money_values(row.get("raw")) if value > 0]
            tax = money(row.get("tax"))
            if not tax and len(raw_values) >= 2:
                # In the scraped part-sale report, tax sometimes lands in source/raw
                # columns. Keep this conservative: only infer obvious 7%-ish tax.
                candidates = [value for value in raw_values if value < total and value <= total * Decimal("0.10")]
                tax = max(candidates or [Decimal("0.00")])
            parts = total - tax
            rows.append({"month": month_key(row.get("closed_date")), "type": "PS", "service": Decimal("0.00"), "parts": parts, "tax": tax, "total": total})
    return rows


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
    accounts = execute(
        models,
        db,
        uid,
        api_key,
        "account.account",
        "search_read",
        [[("company_ids", "in", [company_id]), ("name", "in", ["Parts Revenue", "Service Revenue", "Rental Revenue"])]],
        {"fields": ["id", "name"]},
    )
    account_names = {row["id"]: row["name"] for row in accounts}
    lines = execute(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        "search_read",
        [
            [
                ("company_id", "=", company_id),
                ("parent_state", "=", "posted"),
                ("date", ">=", "2026-01-01"),
                ("date", "<", "2026-08-01"),
                ("account_id", "in", list(account_names)),
            ]
        ],
        {"fields": ["date", "account_id", "balance"], "limit": 20000},
    )
    odoo = defaultdict(lambda: defaultdict(Decimal))
    for line in lines:
        month = str(line["date"])[:7]
        account = line["account_id"][1]
        odoo[month][account] += Decimal(str(-float(line["balance"]))).quantize(Decimal("0.01"))

    shop = defaultdict(lambda: defaultdict(Decimal))
    counts = defaultdict(int)
    for row in read_shop_boss():
        counts[row["month"]] += 1
        shop[row["month"]]["Shop Boss Parts"] += row["parts"]
        shop[row["month"]]["Shop Boss Service"] += row["service"]
        shop[row["month"]]["Shop Boss Tax"] += row["tax"]
        shop[row["month"]]["Shop Boss Total"] += row["total"]

    months = sorted(set(shop) | set(odoo))
    rows = []
    for month in months:
        shop_revenue = shop[month]["Shop Boss Parts"] + shop[month]["Shop Boss Service"]
        odoo_revenue = odoo[month]["Parts Revenue"] + odoo[month]["Service Revenue"] + odoo[month]["Rental Revenue"]
        rows.append(
            {
                "month": month,
                "shop_boss_docs": counts[month],
                "shop_boss_parts": f"{float(shop[month]['Shop Boss Parts']):.2f}",
                "shop_boss_service": f"{float(shop[month]['Shop Boss Service']):.2f}",
                "shop_boss_tax": f"{float(shop[month]['Shop Boss Tax']):.2f}",
                "shop_boss_revenue_ex_tax": f"{float(shop_revenue):.2f}",
                "shop_boss_total": f"{float(shop[month]['Shop Boss Total']):.2f}",
                "odoo_parts": f"{float(odoo[month]['Parts Revenue']):.2f}",
                "odoo_service": f"{float(odoo[month]['Service Revenue']):.2f}",
                "odoo_rental": f"{float(odoo[month]['Rental Revenue']):.2f}",
                "odoo_revenue": f"{float(odoo_revenue):.2f}",
                "revenue_gap_shop_minus_odoo": f"{float(shop_revenue - odoo_revenue):.2f}",
            }
        )
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else []
    with DETAIL.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    total_shop_revenue = sum(money(row["shop_boss_revenue_ex_tax"]) for row in rows)
    total_odoo_revenue = sum(money(row["odoo_revenue"]) for row in rows)
    summary = [
        "# All Available Shop Boss vs Odoo Revenue",
        "",
        f"- Shop Boss revenue excluding tax: ${float(total_shop_revenue):,.2f}",
        f"- Odoo posted Parts/Service/Rental revenue: ${float(total_odoo_revenue):,.2f}",
        f"- Gap, Shop Boss minus Odoo: ${float(total_shop_revenue - total_odoo_revenue):,.2f}",
        "",
        "## By Month",
        "",
        *[
            f"- {row['month']}: Shop Boss revenue ${float(row['shop_boss_revenue_ex_tax']):,.2f}; Odoo revenue ${float(row['odoo_revenue']):,.2f}; gap ${float(row['revenue_gap_shop_minus_odoo']):,.2f}; docs {row['shop_boss_docs']}"
            for row in rows
        ],
    ]
    SUMMARY.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(SUMMARY)
    print(f"Shop Boss revenue ex tax ${float(total_shop_revenue):,.2f}")
    print(f"Odoo revenue ${float(total_odoo_revenue):,.2f}")
    print(f"Gap ${float(total_shop_revenue - total_odoo_revenue):,.2f}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
