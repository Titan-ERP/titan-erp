import argparse
import csv
import os
import re
import xmlrpc.client
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "accounting"
COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Bank"

DEPOSIT_RE = re.compile(r"BANKCARD.*MTOT DEP|MERCHANT DEPOSIT|MERCHANT SERVICE/NET SETTLE|\bDEPOSIT\b", re.I)
CHECK_RE = re.compile(r"\bCHECK\b|TELLER CHECK|INTUIT.*/CHECKS", re.I)
LOAN_RE = re.compile(r"TELEPHONE TRF TO LN|ATS - CHECKING TO LN|\bLOAN\b", re.I)
CARD_PAYMENT_RE = re.compile(
    r"\bDISCOVER\b|\bCAPITAL ONE\b|\bCHASE\b|\bAMEX\b|\bCREDIT ONE\b|\bCREDIT CARD\b|PAYMENT THANK YOU|AUTOPAY PAYMENT",
    re.I,
)
TAX_RE = re.compile(r"IRS|MSDEPTOFREVENUE|TAXPAYMENT|MDES/TAXDRAFT", re.I)
PAYROLL_RE = re.compile(r"PAYROLL|QUICKBOOKS|INTUIT", re.I)

SAFE_VENDOR_RULES = [
    (r"INTEREST PAYMENT", {"Interest Income"}),
    (r"STOP/HOLD FEE|MONTHLY DEBIT CARD FEE", {"Bank Merchant Fees"}),
    (r"GOOGLE \*WORKSPACE|GOOGLE WORKSPACE|BLS\*SHOP BOSS|WWW\.SMALINK\.COM|VONAGE BUSINESS", {"Software Subscriptions"}),
    (r"UPS\*|PAYPAL \*UPS|USPS PO|WAL WAL-MART|DOLLAR GENERAL|AMAZON\.COM", {"Office Expenses"}),
    (r"DIXIE ELECTRIC", {"Facility Expense"}),
    (r"CLARK'?S #49|CIRCLE K|MARATHON|MINIT MART|MACS #|HAYDEN VALERO", {"Company Vehicle Expense"}),
    (r"SUBWAY|FIREHOUSE SUBS|JULIA'?SSTEAKHOUSE|COCA COLA", {"Meals & Entertainment"}),
    (r"SANDHILLS GLOBAL", {"Marketing & Advertising"}),
    (
        r"SOUTHERN-GLOBAL\.COM|SHOUP MANUFACTURING|SCOTT EQUIPMENT|SCOTTS HYDRAULIC|MEGA PARTS|"
        r"FARMLAND TRACTOR|DARRELL HARP|HEAVY EQUIPMENT SPECI|SPAREX AURORA|PAYPAL \*STARTFABRIK|"
        r"FRIDAYPARTS|COLE TRACTOR|SQ \*WEST VIRGINIA MANUFAC|TRACTORPARTS|BT \*DB ELECTRICAL|BROCE MANUFACTURING|DITCH WITCH",
        {"Parts COGS"},
    ),
    (r"PAYPAL \*DELL|UPLIFT DESK|PAYPAL \*HERMAN MILL|APPLE STORE", {"Shop & Service Equipment"}),
]

REVENUE_OR_RECEIVABLE_ACCOUNTS = {
    "Parts Revenue",
    "Service Revenue",
    "Accounts Receivable",
    "Sales Tax Payable",
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


def rel_id(value):
    return value[0] if isinstance(value, list) and value else False


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


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


def get_company_journal(models, db, uid, api_key):
    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id", "name"], "limit": 1})
    if not company:
        raise SystemExit(f"Company not found: {COMPANY}")
    journal = execute(
        models,
        db,
        uid,
        api_key,
        "account.journal",
        "search_read",
        [[("name", "=", JOURNAL), ("company_id", "=", company[0]["id"])]],
        {"fields": ["id", "name"], "limit": 1},
    )
    if not journal:
        raise SystemExit(f"Journal not found for {COMPANY}: {JOURNAL}")
    return company[0], journal[0]


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fetch_reconciled_bank_lines(models, db, uid, api_key, company_id, journal_id, date_from, date_to):
    return execute(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        "search_read",
        [[
            ("company_id", "=", company_id),
            ("journal_id", "=", journal_id),
            ("date", ">=", date_from),
            ("date", "<", date_to),
            ("is_reconciled", "=", True),
        ]],
        {
            "fields": ["id", "date", "amount", "payment_ref", "partner_id", "move_id", "is_reconciled"],
            "limit": 50000,
            "order": "date asc,id asc",
        },
    )


def fetch_move_lines(models, db, uid, api_key, move_ids):
    if not move_ids:
        return []
    return execute(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        "search_read",
        [[("move_id", "in", move_ids)]],
        {
            "fields": [
                "id",
                "move_id",
                "date",
                "name",
                "ref",
                "partner_id",
                "account_id",
                "debit",
                "credit",
                "balance",
                "amount_residual",
                "reconciled",
                "matching_number",
                "full_reconcile_id",
            ],
            "limit": 100000,
            "order": "move_id,id",
        },
    )


def fetch_account_types(models, db, uid, api_key, account_ids):
    if not account_ids:
        return {}
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "account.account",
        "search_read",
        [[("id", "in", sorted(account_ids))]],
        {"fields": ["id", "code", "name", "account_type"], "limit": 10000},
    )
    return {row["id"]: row for row in rows}


def is_bank_line(move_line, account_by_id):
    account_id = rel_id(move_line.get("account_id"))
    account = account_by_id.get(account_id, {})
    account_type = account.get("account_type") or ""
    account_name = account.get("name") or rel_name(move_line.get("account_id"))
    return account_type == "asset_cash" or "Operating Checking" in account_name


def expected_accounts(ref):
    found = set()
    for pattern, accounts in SAFE_VENDOR_RULES:
        if re.search(pattern, ref or "", re.I):
            found.update(accounts)
    return found


def classify_bank_ref(ref):
    if DEPOSIT_RE.search(ref or ""):
        return "Customer/card deposit or merchant settlement"
    if CHECK_RE.search(ref or ""):
        return "Check"
    if LOAN_RE.search(ref or ""):
        return "Loan transfer/payment"
    if CARD_PAYMENT_RE.search(ref or ""):
        return "Credit-card payment"
    if TAX_RE.search(ref or ""):
        return "Tax payment"
    if PAYROLL_RE.search(ref or ""):
        return "Payroll/vendor payroll"
    if expected_accounts(ref):
        return "Known operating vendor"
    return "Other reconciled item"


def audit_line(bank, counterpart_lines, account_by_id):
    ref = bank.get("payment_ref") or ""
    amount = money(bank.get("amount"))
    account_names = [rel_name(line.get("account_id")) for line in counterpart_lines]
    account_types = [account_by_id.get(rel_id(line.get("account_id")), {}).get("account_type", "") for line in counterpart_lines]
    account_set = set(account_names)
    category = classify_bank_ref(ref)

    if not counterpart_lines:
        return "High", category, "Reconciled bank line has no non-bank counterpart line.", "Review the journal entry and rebuild the bank match."

    if "Bank Suspense Account" in account_set:
        return "High", category, "Reconciled line still has Bank Suspense Account as a counterpart.", "Undo and recode the suspense line to the correct account."

    if DEPOSIT_RE.search(ref) and amount > 0 and (account_set & REVENUE_OR_RECEIVABLE_ACCOUNTS or any(kind == "income" for kind in account_types)):
        return (
            "High",
            category,
            "Deposit/merchant settlement is reconciled directly to revenue, receivable, tax, or another income account.",
            "Review against Shop Boss payments/card batch. Use clearing/payment matching so revenue is not duplicated.",
        )

    if re.search(r"MERCHANT SERVICE/NET SETTLE", ref, re.I) and amount > 0:
        return (
            "High",
            category,
            "Merchant service net settlement is positive and needs batch-level Shop Boss/card verification.",
            "Match to the card batch and merchant fees rather than treating it as standalone revenue.",
        )

    if CHECK_RE.search(ref) and not rel_name(bank.get("partner_id")):
        return "Medium", category, "Check is reconciled without a bank-line partner.", "Confirm payee/check-register detail and attach partner if available."

    if LOAN_RE.search(ref):
        if not any("Loan" in name or "Note" in name or "Interest" in name for name in account_names):
            return "Medium", category, "Loan-looking bank line is reconciled without an obvious loan principal or interest account.", "Verify principal/interest split."

    if CARD_PAYMENT_RE.search(ref):
        if not any("Credit Card" in name or "Discover" in name or "Capital One" in name or "Chase" in name or "AMEX" in name for name in account_names):
            return "Medium", category, "Credit-card-looking payment is reconciled without an obvious card liability account.", "Verify card liability/payment matching."

    expected = expected_accounts(ref)
    if expected and not (account_set & expected):
        return (
            "Medium",
            category,
            f"Known vendor reference expected {', '.join(sorted(expected))}, but matched to {', '.join(account_names)}.",
            "Review whether this should be recoded to the expected operating account.",
        )

    if len(counterpart_lines) > 2:
        return "Low", category, "Reconciled bank line has more than two non-bank counterpart lines.", "Spot-check split coding."

    return "OK", category, "No obvious issue detected by automated audit rules.", ""


def build_rows(bank_lines, move_lines, account_by_id):
    by_move = defaultdict(list)
    for line in move_lines:
        by_move[rel_id(line.get("move_id"))].append(line)

    detail_rows = []
    summary_counter = Counter()
    for bank in bank_lines:
        lines = by_move.get(rel_id(bank.get("move_id")), [])
        counterpart_lines = [line for line in lines if not is_bank_line(line, account_by_id)]
        risk, category, finding, action = audit_line(bank, counterpart_lines, account_by_id)
        account_names = [rel_name(line.get("account_id")) for line in counterpart_lines]
        partner_names = sorted({rel_name(line.get("partner_id")) for line in counterpart_lines if rel_name(line.get("partner_id"))})
        summary_counter[(risk, category, finding)] += 1
        detail_rows.append({
            "Risk": risk,
            "Category": category,
            "Finding": finding,
            "Recommended Action": action,
            "Bank Statement Line ID": bank["id"],
            "Date": bank.get("date", ""),
            "Amount": float(money(bank.get("amount"))),
            "Payment Ref": bank.get("payment_ref") or "",
            "Bank Partner": rel_name(bank.get("partner_id")),
            "Bank Move": rel_name(bank.get("move_id")),
            "Counterpart Count": len(counterpart_lines),
            "Counterpart Accounts": "; ".join(account_names),
            "Counterpart Partners": "; ".join(partner_names),
            "Counterpart Move Line IDs": "; ".join(str(line["id"]) for line in counterpart_lines),
            "Full Reconcile IDs": "; ".join(sorted({rel_name(line.get("full_reconcile_id")) for line in counterpart_lines if rel_name(line.get("full_reconcile_id"))})),
            "Matching Numbers": "; ".join(sorted({str(line.get("matching_number") or "") for line in counterpart_lines if line.get("matching_number")})),
        })

    summary_rows = []
    for (risk, category, finding), count in summary_counter.items():
        amount = sum(money(row["Amount"]) for row in detail_rows if row["Risk"] == risk and row["Category"] == category and row["Finding"] == finding)
        summary_rows.append({
            "Risk": risk,
            "Category": category,
            "Finding": finding,
            "Count": count,
            "Net Amount": float(amount),
        })
    risk_order = {"High": 0, "Medium": 1, "Low": 2, "OK": 3}
    detail_rows.sort(key=lambda row: (risk_order.get(row["Risk"], 9), row["Date"], row["Bank Statement Line ID"]))
    summary_rows.sort(key=lambda row: (risk_order.get(row["Risk"], 9), row["Category"], row["Finding"]))
    return detail_rows, summary_rows


def write_summary_md(path, args, detail_rows, summary_rows):
    risk_counts = Counter(row["Risk"] for row in detail_rows)
    lines = [
        "# Reconciled Bank Matching Audit",
        "",
        f"Company: {COMPANY}",
        f"Journal: {JOURNAL}",
        f"Date range: {args.date_from} to {args.date_to} exclusive",
        "",
        "## Result",
        "",
        f"- Reconciled bank lines audited: {len(detail_rows)}",
        f"- High-risk matches: {risk_counts.get('High', 0)}",
        f"- Medium-risk matches: {risk_counts.get('Medium', 0)}",
        f"- Low-risk matches: {risk_counts.get('Low', 0)}",
        f"- OK matches: {risk_counts.get('OK', 0)}",
        "",
        "## High And Medium Findings",
        "",
    ]
    important = [row for row in summary_rows if row["Risk"] in {"High", "Medium"}]
    if important:
        for row in important:
            amount = Decimal(str(row["Net Amount"])).quantize(Decimal("0.01"))
            lines.append(f"- {row['Risk']} - {row['Category']}: {row['Count']} lines, net ${amount:,.2f}. {row['Finding']}")
    else:
        lines.append("- No high or medium findings.")
    lines.extend([
        "",
        "## Files",
        "",
        "- Detail CSV: `odoo_imports/accounting/reconciled_bank_matching_audit_2026_detail.csv`",
        "- Summary CSV: `odoo_imports/accounting/reconciled_bank_matching_audit_2026_summary.csv`",
        "- Review queue CSV: `odoo_imports/accounting/reconciled_bank_matching_audit_2026_review_queue.csv`",
        "",
        "This audit is read-only. It does not undo, recode, or reconcile any bank lines.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Audit currently reconciled Laurel bank matches for the year.")
    parser.add_argument("--date-from", default="2026-01-01")
    parser.add_argument("--date-to", default="2027-01-01")
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    company, journal = get_company_journal(models, db, uid, api_key)
    bank_lines = fetch_reconciled_bank_lines(models, db, uid, api_key, company["id"], journal["id"], args.date_from, args.date_to)
    move_ids = [rel_id(line.get("move_id")) for line in bank_lines if rel_id(line.get("move_id"))]
    move_lines = fetch_move_lines(models, db, uid, api_key, move_ids)
    account_ids = {rel_id(line.get("account_id")) for line in move_lines if rel_id(line.get("account_id"))}
    account_by_id = fetch_account_types(models, db, uid, api_key, account_ids)

    detail_rows, summary_rows = build_rows(bank_lines, move_lines, account_by_id)
    detail_path = OUT_DIR / "reconciled_bank_matching_audit_2026_detail.csv"
    summary_path = OUT_DIR / "reconciled_bank_matching_audit_2026_summary.csv"
    review_path = OUT_DIR / "reconciled_bank_matching_audit_2026_review_queue.csv"
    md_path = OUT_DIR / "reconciled_bank_matching_audit_2026.md"

    write_csv(
        detail_path,
        detail_rows,
        [
            "Risk",
            "Category",
            "Finding",
            "Recommended Action",
            "Bank Statement Line ID",
            "Date",
            "Amount",
            "Payment Ref",
            "Bank Partner",
            "Bank Move",
            "Counterpart Count",
            "Counterpart Accounts",
            "Counterpart Partners",
            "Counterpart Move Line IDs",
            "Full Reconcile IDs",
            "Matching Numbers",
        ],
    )
    write_csv(summary_path, summary_rows, ["Risk", "Category", "Finding", "Count", "Net Amount"])
    write_csv(
        review_path,
        [row for row in detail_rows if row["Risk"] != "OK"],
        [
            "Risk",
            "Category",
            "Date",
            "Amount",
            "Payment Ref",
            "Counterpart Accounts",
            "Bank Partner",
            "Counterpart Partners",
            "Finding",
            "Recommended Action",
            "Bank Statement Line ID",
            "Counterpart Move Line IDs",
        ],
    )
    write_summary_md(md_path, args, detail_rows, summary_rows)

    counts = Counter(row["Risk"] for row in detail_rows)
    print(f"Connected uid: {uid}")
    print(f"Database: {db}")
    print(f"Company: {company['name']}")
    print(f"Range: {args.date_from} to {args.date_to} exclusive")
    print(f"Reconciled bank lines audited: {len(detail_rows)}")
    print(f"Risk counts: {dict(counts)}")
    print(f"Detail: {detail_path}")
    print(f"Summary: {summary_path}")
    print(f"Review queue: {review_path}")
    print(f"Markdown: {md_path}")


if __name__ == "__main__":
    main()
