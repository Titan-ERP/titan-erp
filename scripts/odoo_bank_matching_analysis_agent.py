import argparse
import csv
import os
import re
import xmlrpc.client
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_ROOT = ROOT / "odoo_imports" / "accounting" / "bank_matching_analysis"
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
GENERIC_PARTNERS = {"", "Customer/Card Deposits", "cash", "Walkins Cash"}
ACCEPTED_DIRECT_DEPOSIT_ACCOUNTS = {"Parts Revenue", "Gain on Asset Disposal"}
SUSPICIOUS_DIRECT_DEPOSIT_ACCOUNTS = {"Service Revenue", "Accounts Receivable", "Sales Tax Payable"}


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
    company = execute(
        models,
        db,
        uid,
        api_key,
        "res.company",
        "search_read",
        [[("name", "=", COMPANY)]],
        {"fields": ["id", "name"], "limit": 1},
    )
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
        raise SystemExit(f"Journal not found: {JOURNAL}")
    return company[0], journal[0]


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def fetch_bank_lines(models, db, uid, api_key, company_id, journal_id, date_from, date_to):
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
            ("date", "<=", date_to),
        ]],
        {
            "fields": ["id", "date", "amount", "payment_ref", "partner_id", "move_id", "is_reconciled"],
            "limit": 100000,
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
                "partner_id",
                "account_id",
                "debit",
                "credit",
                "balance",
                "matching_number",
                "full_reconcile_id",
            ],
            "limit": 200000,
            "order": "move_id,id",
        },
    )


def fetch_accounts(models, db, uid, api_key, account_ids):
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


def is_bank_counterpart(line, account_by_id):
    account = account_by_id.get(rel_id(line.get("account_id")), {})
    name = account.get("name") or rel_name(line.get("account_id"))
    return account.get("account_type") == "asset_cash" or "Operating Checking" in name


def classify_ref(ref):
    if DEPOSIT_RE.search(ref or ""):
        return "Deposit / merchant settlement"
    if CHECK_RE.search(ref or ""):
        return "Check"
    if LOAN_RE.search(ref or ""):
        return "Loan transfer/payment"
    if CARD_PAYMENT_RE.search(ref or ""):
        return "Credit card payment"
    if TAX_RE.search(ref or ""):
        return "Tax"
    if PAYROLL_RE.search(ref or ""):
        return "Payroll"
    return "Other"


def add_issue(rows, risk, category, bank, counterpart_lines, finding, action):
    rows.append({
        "Risk": risk,
        "Category": category,
        "Bank Statement Line ID": bank["id"],
        "Date": bank.get("date", ""),
        "Amount": float(money(bank.get("amount"))),
        "Payment Ref": bank.get("payment_ref") or "",
        "Bank Partner": rel_name(bank.get("partner_id")),
        "Counterpart Accounts": "; ".join(rel_name(line.get("account_id")) for line in counterpart_lines),
        "Counterpart Partners": "; ".join(sorted({rel_name(line.get("partner_id")) for line in counterpart_lines if rel_name(line.get("partner_id"))})),
        "Counterpart Move Line IDs": "; ".join(str(line["id"]) for line in counterpart_lines),
        "Finding": finding,
        "Recommended Action": action,
    })


def build_analysis(bank_lines, move_lines, account_by_id):
    by_move = defaultdict(list)
    for line in move_lines:
        by_move[rel_id(line.get("move_id"))].append(line)

    issues = []
    account_totals = Counter()
    account_counts = Counter()
    category_counts = Counter()
    duplicate_keys = defaultdict(list)
    missing_partner_count = 0

    for bank in bank_lines:
        ref = bank.get("payment_ref") or ""
        amount = money(bank.get("amount"))
        category = classify_ref(ref)
        category_counts[category] += 1
        duplicate_keys[(bank.get("date"), float(amount), ref.strip().upper())].append(bank["id"])
        if not rel_name(bank.get("partner_id")):
            missing_partner_count += 1

        lines = by_move.get(rel_id(bank.get("move_id")), [])
        counterpart_lines = [line for line in lines if not is_bank_counterpart(line, account_by_id)]
        account_names = [rel_name(line.get("account_id")) for line in counterpart_lines]
        account_types = [account_by_id.get(rel_id(line.get("account_id")), {}).get("account_type", "") for line in counterpart_lines]
        ar_invoice_settlement = bool(counterpart_lines) and all(
            rel_name(line.get("account_id")) == "Accounts Receivable" and rel_id(line.get("full_reconcile_id"))
            for line in counterpart_lines
        )

        for line in counterpart_lines:
            account = rel_name(line.get("account_id"))
            account_counts[account] += 1
            account_totals[account] += money(line.get("credit")) - money(line.get("debit"))

        if not bank.get("is_reconciled"):
            add_issue(issues, "High", category, bank, counterpart_lines, "Bank line is still open/unreconciled.", "Reconcile or explicitly code this bank line.")
        if "Bank Suspense Account" in account_names:
            add_issue(issues, "High", category, bank, counterpart_lines, "Bank line still uses Bank Suspense Account.", "Recode the suspense counterpart.")
        direct_deposit_accounts = set(account_names) - ACCEPTED_DIRECT_DEPOSIT_ACCOUNTS
        suspicious_direct_deposit = any(name in SUSPICIOUS_DIRECT_DEPOSIT_ACCOUNTS for name in direct_deposit_accounts) or any(
            kind.startswith("income") and name not in ACCEPTED_DIRECT_DEPOSIT_ACCOUNTS
            for kind, name in zip(account_types, account_names)
        )
        if DEPOSIT_RE.search(ref) and amount > 0 and suspicious_direct_deposit and not ar_invoice_settlement:
            add_issue(
                issues,
                "Medium",
                category,
                bank,
                counterpart_lines,
                "Deposit is coded directly to revenue/receivable/tax.",
                "Accept only if this is the simplified bank-ready method; otherwise match to Shop Boss payments and merchant fees.",
            )
        if (
            re.search(r"MERCHANT SERVICE/NET SETTLE", ref, re.I)
            and amount > 0
            and "Bank Merchant Fees" not in account_names
            and not ar_invoice_settlement
        ):
            add_issue(issues, "Medium", category, bank, counterpart_lines, "Merchant net settlement has no fee split on the bank move.", "Check whether merchant service fees were posted separately.")
        if CHECK_RE.search(ref) and not rel_name(bank.get("partner_id")):
            risk = "High" if abs(amount) >= Decimal("5000") else "Medium"
            add_issue(issues, risk, category, bank, counterpart_lines, "Check has no bank-line partner/payee.", "Use check register detail if available and attach the payee.")
        if CHECK_RE.search(ref) and abs(amount) >= Decimal("5000") and "Office Expenses" in account_names:
            add_issue(issues, "High", category, bank, counterpart_lines, "Large generic check is coded to Office Expenses.", "Confirm whether this is loan principal, equipment, payroll, owner distribution, or a true operating expense.")
        if LOAN_RE.search(ref) and not any(
            "Loan" in name or "Note" in name or "Interest" in name or "Line of Credit" in name for name in account_names
        ):
            add_issue(issues, "Medium", category, bank, counterpart_lines, "Loan-looking bank line lacks a loan/note/interest account.", "Verify principal and interest split.")

    for key, ids in duplicate_keys.items():
        if len(ids) > 1:
            sample_bank = {"id": ", ".join(map(str, ids)), "date": key[0], "amount": key[1], "payment_ref": key[2], "partner_id": False}
            add_issue(issues, "Low", "Duplicate signal", sample_bank, [], "Same date/ref/amount appears more than once.", "Spot-check that duplicate-looking lines are legitimate separate transactions.")

    return {
        "issues": sorted(issues, key=lambda row: ({"High": 0, "Medium": 1, "Low": 2}.get(row["Risk"], 9), row["Date"], str(row["Bank Statement Line ID"]))),
        "account_totals": account_totals,
        "account_counts": account_counts,
        "category_counts": category_counts,
        "missing_partner_count": missing_partner_count,
    }


def write_markdown(path, args, bank_lines, analysis):
    issues = analysis["issues"]
    risk_counts = Counter(row["Risk"] for row in issues)
    reconciled = sum(1 for row in bank_lines if row.get("is_reconciled"))
    open_count = len(bank_lines) - reconciled
    top_accounts = analysis["account_totals"].most_common(12)
    lines = [
        "# Bank Matching Analysis Agent",
        "",
        f"Company: {COMPANY}",
        f"Journal: {JOURNAL}",
        f"Date range: {args.date_from} to {args.date_to}",
        "",
        "## Current State",
        "",
        f"- Bank lines reviewed: {len(bank_lines)}",
        f"- Reconciled lines: {reconciled}",
        f"- Open/unreconciled lines: {open_count}",
        f"- Lines missing bank-line partner: {analysis['missing_partner_count']}",
        f"- High-risk findings: {risk_counts.get('High', 0)}",
        f"- Medium-risk findings: {risk_counts.get('Medium', 0)}",
        f"- Low-risk findings: {risk_counts.get('Low', 0)}",
        "",
        "## Inefficiencies Found",
        "",
    ]
    if issues:
        grouped = Counter((row["Risk"], row["Finding"]) for row in issues)
        for (risk, finding), count in grouped.most_common(12):
            lines.append(f"- {risk}: {count} lines - {finding}")
    else:
        lines.append("- No rule-based bank matching inefficiencies found.")

    lines.extend(["", "## Account Concentration", ""])
    for account, net in top_accounts:
        count = analysis["account_counts"][account]
        lines.append(f"- {account or 'Blank'}: {count} lines, net ${Decimal(net):,.2f}")

    lines.extend([
        "",
        "## Output Files",
        "",
        "- Review queue: `bank_matching_analysis_review_queue.csv`",
        "- Account summary: `bank_matching_analysis_account_summary.csv`",
        "",
        "This agent is read-only. It reports matching risks and inefficiencies; it does not edit Odoo.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Read-only Odoo bank matching analysis agent.")
    parser.add_argument("--date-from", default="2026-01-01")
    parser.add_argument("--date-to", default=date.today().isoformat())
    parser.add_argument("--output-date", default=date.today().isoformat())
    args = parser.parse_args()

    db, uid, api_key, models = connect()
    company, journal = get_company_journal(models, db, uid, api_key)
    bank_lines = fetch_bank_lines(models, db, uid, api_key, company["id"], journal["id"], args.date_from, args.date_to)
    move_ids = [rel_id(row.get("move_id")) for row in bank_lines if rel_id(row.get("move_id"))]
    move_lines = fetch_move_lines(models, db, uid, api_key, move_ids)
    account_ids = {rel_id(row.get("account_id")) for row in move_lines if rel_id(row.get("account_id"))}
    account_by_id = fetch_accounts(models, db, uid, api_key, account_ids)
    analysis = build_analysis(bank_lines, move_lines, account_by_id)

    out_dir = OUT_ROOT / args.output_date
    out_dir.mkdir(parents=True, exist_ok=True)
    review_path = out_dir / "bank_matching_analysis_review_queue.csv"
    account_path = out_dir / "bank_matching_analysis_account_summary.csv"
    md_path = out_dir / "bank_matching_analysis_summary.md"

    review_fields = [
        "Risk",
        "Category",
        "Bank Statement Line ID",
        "Date",
        "Amount",
        "Payment Ref",
        "Bank Partner",
        "Counterpart Accounts",
        "Counterpart Partners",
        "Counterpart Move Line IDs",
        "Finding",
        "Recommended Action",
    ]
    write_csv(review_path, analysis["issues"], review_fields)

    account_rows = [
        {"Account": account, "Count": analysis["account_counts"][account], "Net Amount": float(total)}
        for account, total in analysis["account_totals"].most_common()
    ]
    write_csv(account_path, account_rows, ["Account", "Count", "Net Amount"])
    write_markdown(md_path, args, bank_lines, analysis)

    risk_counts = Counter(row["Risk"] for row in analysis["issues"])
    print(f"Connected uid: {uid}")
    print(f"Database: {db}")
    print(f"Bank lines reviewed: {len(bank_lines)}")
    print(f"Risk counts: {dict(risk_counts)}")
    print(f"Review queue: {review_path}")
    print(f"Account summary: {account_path}")
    print(f"Markdown summary: {md_path}")


if __name__ == "__main__":
    main()
