import argparse
import csv
import os
import re
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "accounting"
DEFAULT_LINE_PATH = OUT_DIR / "live_unreconciled_bank_statement_lines.csv"
DEFAULT_SUMMARY_PATH = OUT_DIR / "live_reconciliation_queue_summary.csv"


RULES = [
    (r"BANKCARD.*MTOT DEP|MERCHANT DEPOSIT|DEPOSIT", "Customer/card deposit", "Match to Shop Boss/POS/card batch and merchant fees before coding."),
    (r"\bCHECK\s+\d+\s+-", "Identified check", "Use check number/payee detail to match vendor bill/payment or code expense."),
    (r"\bCHECK\b|TELLER CHECK|INTUIT.*/CHECKS", "Check needing detail", "Review statement image or check register for check number and payee."),
    (r"TELEPHONE TRF TO LN|ATS - CHECKING TO LN|LOAN", "Loan transfer/payment", "Split principal and interest or match loan liability movement."),
    (r"IRS|MSDEPTOFREVENUE|TAXPAYMENT", "Tax payment", "Match to payroll/sales tax liability account before reconciling."),
    (r"PAYROLL|QUICKBOOKS|INTUIT", "Payroll/vendor payroll", "Match payroll clearing or payroll liability entries."),
    (r"DISCOVER|CAPITAL ONE|CHASE|AMEX|CREDIT ONE|CREDIT CARD", "Credit-card liability payment", "Match to credit-card liability account/payment."),
    (r"UPS|FEDEX|USPS", "Shipping", "Code to shipping/freight or match vendor bill."),
    (r"GOOGLE WORKSPACE|VONAGE|SHOP BOSS", "Recurring software/service", "Create or use recurring vendor expense rule after account confirmation."),
    (r"STATE FARM|ALFA MUTUAL|AFCO", "Insurance/finance payment", "Match vendor bill or insurance/finance liability account."),
]


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env(ENV_PATH)
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def merchant_key(ref):
    text = str(ref or "").upper()
    text = re.sub(r"\*+\d+", " ", text)
    text = re.sub(r"\b\d{2}/\d{2}\b.*$", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    text = re.sub(r"\b(POS PURCHASE|NON PIN|NON-|PIN|DEBIT|CARD|PURCHASE|CHECKCARD)\b", " ", text)
    text = re.sub(r"[^A-Z0-9&/# -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(text.split()[:7]) or "Unclear"


def classify(ref):
    text = str(ref or "").upper()
    for pattern, bucket, next_action in RULES:
        if re.search(pattern, text):
            return bucket, next_action
    return "Needs account/payee review", "Decide payee/category or match an existing bill/payment."


def write_csv(path, rows, fields):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def month_bounds(month):
    year, month_number = [int(part) for part in month.split("-", 1)]
    if month_number == 12:
        return f"{year}-12-01", f"{year + 1}-01-01"
    return f"{year}-{month_number:02d}-01", f"{year}-{month_number + 1:02d}-01"


def dated_paths(month):
    suffix = month.replace("-", "_")
    return (
        OUT_DIR / f"live_unreconciled_bank_statement_lines_{suffix}.csv",
        OUT_DIR / f"live_reconciliation_queue_summary_{suffix}.csv",
    )


def main():
    parser = argparse.ArgumentParser(description="Export unreconciled Odoo bank statement lines as a reconciliation queue.")
    parser.add_argument("--month", help="Limit to one month in YYYY-MM format, for example 2026-07.")
    parser.add_argument("--date-from", dest="date_from", help="Inclusive start date in YYYY-MM-DD format.")
    parser.add_argument("--date-to", dest="date_to", help="Exclusive end date in YYYY-MM-DD format.")
    args = parser.parse_args()

    if args.month and (args.date_from or args.date_to):
        raise SystemExit("Use either --month or --date-from/--date-to, not both.")

    date_from = args.date_from
    date_to = args.date_to
    line_path = DEFAULT_LINE_PATH
    summary_path = DEFAULT_SUMMARY_PATH
    if args.month:
        date_from, date_to = month_bounds(args.month)
        line_path, summary_path = dated_paths(args.month)
    elif date_from or date_to:
        suffix = f"{date_from or 'start'}_to_{date_to or 'end'}".replace("-", "_")
        line_path = OUT_DIR / f"live_unreconciled_bank_statement_lines_{suffix}.csv"
        summary_path = OUT_DIR / f"live_reconciliation_queue_summary_{suffix}.csv"

    db, uid, api_key, models = connect()
    domain = [("is_reconciled", "=", False)]
    if date_from:
        domain.append(("date", ">=", date_from))
    if date_to:
        domain.append(("date", "<", date_to))
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        "search_read",
        [domain],
        {
            "fields": ["id", "date", "journal_id", "company_id", "amount", "payment_ref", "partner_id", "move_id"],
            "limit": 20000,
            "order": "date asc, id asc",
        },
    )

    line_rows = []
    groups = defaultdict(list)
    for row in rows:
        bucket, next_action = classify(row.get("payment_ref"))
        key = merchant_key(row.get("payment_ref"))
        line = {
            "Bank Statement Line ID": row["id"],
            "Date": row.get("date"),
            "Company": rel(row.get("company_id")),
            "Journal": rel(row.get("journal_id")),
            "Partner": rel(row.get("partner_id")),
            "Amount": float(money(row.get("amount"))),
            "Payment Ref": row.get("payment_ref") or "",
            "Bank Move": rel(row.get("move_id")),
            "Bucket": bucket,
            "Merchant Key": key,
            "Next Action": next_action,
        }
        line_rows.append(line)
        groups[(line["Company"], line["Journal"], bucket, key, next_action)].append(line)

    summary_rows = []
    for (company, journal, bucket, key, next_action), group_rows in groups.items():
        total = sum(money(row["Amount"]) for row in group_rows)
        summary_rows.append(
            {
                "Company": company,
                "Journal": journal,
                "Bucket": bucket,
                "Merchant Key": key,
                "Count": len(group_rows),
                "Net Amount": float(total),
                "Next Action": next_action,
                "Example": group_rows[0]["Payment Ref"],
            }
        )
    summary_rows.sort(key=lambda row: (row["Company"], row["Journal"], row["Bucket"], -row["Count"], row["Merchant Key"]))

    write_csv(
        line_path,
        line_rows,
        ["Bank Statement Line ID", "Date", "Company", "Journal", "Partner", "Amount", "Payment Ref", "Bank Move", "Bucket", "Merchant Key", "Next Action"],
    )
    write_csv(
        summary_path,
        summary_rows,
        ["Company", "Journal", "Bucket", "Merchant Key", "Count", "Net Amount", "Next Action", "Example"],
    )

    print(f"Connected uid: {uid}")
    print(f"Database: {db}")
    if date_from or date_to:
        print(f"Date range: {date_from or 'beginning'} to {date_to or 'end'} (end exclusive)")
    print(f"Unreconciled lines exported: {len(line_rows)}")
    print(f"Queue groups exported: {len(summary_rows)}")
    print(f"Lines: {line_path}")
    print(f"Summary: {summary_path}")


if __name__ == "__main__":
    main()
