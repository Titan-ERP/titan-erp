import csv
import os
import re
import sys
import xmlrpc.client
from datetime import datetime
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_BOSS = ROOT / "odoo_imports" / "shop_boss"
OUT_DIR = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26"
RO_FILE = SHOP_BOSS / "shop_boss_finalized_ro_rows_ytd_2026.csv"
PS_FILE = SHOP_BOSS / "shop_boss_part_sale_rows_ytd_2026.csv"
DETAIL = OUT_DIR / "all_available_shop_boss_invoice_coverage_audit.csv"
SUMMARY = OUT_DIR / "all_available_shop_boss_invoice_coverage_summary.md"
COMPANY_NAME = "Southern Equipment Company (Laurel)"
STOP_WORDS = {"AND", "THE", "INC", "LLC", "CO", "CORP", "COMPANY", "CASH", "WALKINS", "WALKIN", "DBA", "OF", "MS"}


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
    text = str(value or "").replace("$", "").replace(",", "").replace("\x02", "").replace("\x03", "").strip()
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


def norm(value):
    text = str(value or "").upper().replace("&", " AND ").replace("\x02", "").replace("\x03", "")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def reverse_comma(name):
    text = str(name or "")
    if "," not in text:
        return text
    left, right = [part.strip() for part in text.split(",", 1)]
    return f"{right} {left}".strip()


def tokens(value):
    return [part for part in norm(value).split() if len(part) >= 2 and part not in STOP_WORDS]


def name_score(shop_customer, odoo_partner):
    hay = set(tokens(odoo_partner))
    best = 0.0
    for candidate in (shop_customer, reverse_comma(shop_customer)):
        need = tokens(candidate)
        if need:
            best = max(best, sum(1 for token in need if token in hay) / len(need))
    return best


def read_docs():
    docs = []
    with RO_FILE.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            total = money(row.get("total_ro"))
            if not total:
                values = [value for value in money_values(row.get("raw")) if value > 0]
                total = max(values or [Decimal("0.00")])
            if total <= 0:
                continue
            docs.append(
                {
                    "type": "RO",
                    "number": str(row.get("ro_number", "")).strip(),
                    "date": str(row.get("final_date", "")).strip(),
                    "customer": str(row.get("customer", "")).strip(),
                    "amount": total,
                }
            )
    with PS_FILE.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            total = money(row.get("total_sale"))
            if not total:
                values = [value for value in money_values(row.get("raw")) if value > 0]
                total = max(values or [Decimal("0.00")])
            if total <= 0:
                continue
            docs.append(
                {
                    "type": "PS",
                    "number": str(row.get("ps_number", "")).strip(),
                    "date": str(row.get("closed_date", "")).strip(),
                    "customer": str(row.get("customer", "")).strip(),
                    "amount": total,
                }
            )
    return docs


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

    invoices = execute(
        models,
        db,
        uid,
        api_key,
        "account.move",
        "search_read",
        [
            [
                ("company_id", "=", company_id),
                ("move_type", "=", "out_invoice"),
                ("state", "!=", "cancel"),
                ("invoice_date", ">=", "2026-01-01"),
                ("invoice_date", "<", "2026-08-01"),
            ]
        ],
        {"fields": ["id", "name", "invoice_date", "partner_id", "amount_total", "ref", "invoice_origin", "payment_state"], "limit": 10000},
    )
    results = []
    for doc in read_docs():
        ref = f"{doc['type']} {doc['number']}"
        compact = f"{doc['type']}{doc['number']}"
        doc_date = parse_date(doc["date"])
        direct = []
        exact = []
        near = []
        for inv in invoices:
            inv_text = f"{inv.get('ref') or ''} {inv.get('invoice_origin') or ''}".upper()
            inv_amount = money(inv.get("amount_total"))
            inv_partner = inv["partner_id"][1] if isinstance(inv.get("partner_id"), list) else ""
            if ref in inv_text or compact in inv_text.replace(" ", ""):
                direct.append(inv)
            if abs(inv_amount - doc["amount"]) <= Decimal("0.02"):
                score = name_score(doc["customer"], inv_partner)
                inv_date = parse_date(inv.get("invoice_date"))
                gap = abs((inv_date - doc_date).days) if inv_date and doc_date else 9999
                if score >= 0.75 and gap <= 31:
                    exact.append(inv)
                elif score >= 0.50 and gap <= 45:
                    near.append(inv)
        if direct:
            status = "matched_by_reference"
            match = direct[0]
        elif exact:
            status = "matched_by_customer_amount_date"
            match = exact[0]
        elif near:
            status = "review_near_match"
            match = near[0]
        else:
            status = "no_odoo_invoice_match"
            match = {}
        results.append(
            {
                "shop_boss_type": doc["type"],
                "shop_boss_number": doc["number"],
                "shop_boss_date": doc["date"],
                "shop_boss_customer": doc["customer"],
                "shop_boss_amount": f"{float(doc['amount']):.2f}",
                "status": status,
                "odoo_invoice": match.get("name", ""),
                "odoo_invoice_id": match.get("id", ""),
                "odoo_date": match.get("invoice_date", ""),
                "odoo_partner": match.get("partner_id", ["", ""])[1] if isinstance(match.get("partner_id"), list) else "",
                "odoo_amount": f"{float(match.get('amount_total') or 0):.2f}" if match else "",
                "payment_state": match.get("payment_state", ""),
            }
        )

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "shop_boss_type",
        "shop_boss_number",
        "shop_boss_date",
        "shop_boss_customer",
        "shop_boss_amount",
        "status",
        "odoo_invoice",
        "odoo_invoice_id",
        "odoo_date",
        "odoo_partner",
        "odoo_amount",
        "payment_state",
    ]
    with DETAIL.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    counts = {}
    totals = {}
    for row in results:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
        totals[row["status"]] = totals.get(row["status"], Decimal("0.00")) + money(row["shop_boss_amount"])
    no_match = [row for row in results if row["status"] == "no_odoo_invoice_match"]
    summary = [
        "# All Available Shop Boss Invoice Coverage Audit",
        "",
        f"- Shop Boss positive finalized/closed documents reviewed: {len(results)}",
        f"- Odoo non-cancelled customer invoices reviewed: {len(invoices)}",
        "",
        "## Status Counts",
        "",
        *[f"- {status}: {counts[status]} / ${float(totals[status]):,.2f}" for status in sorted(counts)],
        "",
        "## Largest Missing Odoo Invoice Matches",
        "",
        *[
            f"- {row['shop_boss_type']}{row['shop_boss_number']} {row['shop_boss_date']} {row['shop_boss_customer']} ${float(row['shop_boss_amount']):,.2f}"
            for row in sorted(no_match, key=lambda item: money(item["shop_boss_amount"]), reverse=True)[:20]
        ],
        "",
        "## Note",
        "",
        "This is a coverage audit only. Missing document-level invoice matches should not be batch-created until bank/revenue postings for the same period are reviewed for possible double-counting.",
    ]
    SUMMARY.write_text("\n".join(summary) + "\n", encoding="utf-8")
    print(SUMMARY)
    for status in sorted(counts):
        print(status, counts[status], f"${float(totals[status]):,.2f}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
