import argparse
import csv
import os
import re
import xmlrpc.client
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_ROOT = ROOT / "odoo_imports" / "accounting" / "daily_auto_reconcile"
COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Bank"

RULES = [
    {"account": "Interest Income", "patterns": [r"\bINTEREST PAYMENT\b"]},
    {"account": "Bank Merchant Fees", "patterns": [r"\bSTOP/HOLD FEE\b", r"\bMONTHLY DEBIT CARD FEE\b"]},
    {"account": "Bank Merchant Fees", "patterns": [r"BANKCARD-1205/MTOT DEP"], "negative_only": True},
    {"account": "Employer Payroll Taxes", "patterns": [r"MDES/TAXDRAFT"]},
    {"account": "Sales Tax Payable", "patterns": [r"IRS/USATAXPYMT", r"MSDEPTOFREVENUE/TAXPAYMENT"]},
    {
        "account": "Software Subscriptions",
        "patterns": [
            r"GOOGLE \*WORKSPACE",
            r"GOOGLE WORKSPACE",
            r"BLS\*SHOP BOSS",
            r"WWW\.SMALINK\.COM",
            r"VONAGE BUSINESS",
        ],
    },
    {"account": "Office Expenses", "patterns": [r"UPS\*", r"PAYPAL \*UPS", r"USPS PO", r"WAL WAL-MART", r"DOLLAR GENERAL", r"AMAZON\.COM"]},
    {"account": "Facility Expense", "patterns": [r"DIXIE ELECTRIC"]},
    {"account": "Company Vehicle Expense", "patterns": [r"CLARK'?S #49", r"CIRCLE K", r"MARATHON", r"MINIT MART", r"MACS #", r"HAYDEN VALERO"]},
    {"account": "Meals & Entertainment", "patterns": [r"SUBWAY", r"FIREHOUSE SUBS", r"JULIA'?SSTEAKHOUSE", r"COCA COLA"]},
    {"account": "Marketing & Advertising", "patterns": [r"SANDHILLS GLOBAL"]},
    {
        "account": "Parts COGS",
        "patterns": [
            r"SOUTHERN-GLOBAL\.COM",
            r"SHOUP MANUFACTURING",
            r"SCOTT EQUIPMENT",
            r"SCOTTS HYDRAULIC",
            r"MEGA PARTS",
            r"FARMLAND TRACTOR",
            r"DARRELL HARP",
            r"HEAVY EQUIPMENT SPECI",
            r"SPAREX AURORA",
            r"PAYPAL \*STARTFABRIK",
            r"FRIDAYPARTS",
            r"COLE TRACTOR",
            r"SQ \*WEST VIRGINIA MANUFAC",
        ],
    },
    {"account": "Shop & Service Equipment", "patterns": [r"PAYPAL \*DELL", r"UPLIFT DESK", r"PAYPAL \*HERMAN MILL", r"APPLE STORE"]},
]

BLOCKED_REVIEW_PATTERNS = [
    r"MERCHANT SERVICE/NET SETTLE",
    r"MERCHANT DEPOSIT/CREDIT",
    r"\bDEPOSIT\b",
    r"TAYLORCONSTCO",
    r"TRANSFER FROM",
    r"TELEPHONE TRF TO LN",
    r"ATS - CHECKING TO LN",
    r"\bCHECK\b",
    r"TELLER CHECK",
    r"PAYMENT THANK YOU",
    r"AUTOPAY PAYMENT",
]


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


def account_domain(models, db, uid, api_key, company_id, account_name):
    fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    domain = [("name", "=", account_name)]
    if "company_ids" in fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in fields:
        domain.append(("company_id", "=", company_id))
    return domain


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


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
        {"fields": ["id"], "limit": 1},
    )
    journal = execute(
        models,
        db,
        uid,
        api_key,
        "account.journal",
        "search_read",
        [[("name", "=", JOURNAL), ("company_id", "=", company[0]["id"])]],
        {"fields": ["id"], "limit": 1},
    )
    if not company or not journal:
        raise SystemExit(f"Could not find company {COMPANY!r} and journal {JOURNAL!r}.")
    return company[0], journal[0]


def load_accounts(models, db, uid, api_key, company_id):
    accounts = {}
    for account_name in sorted({rule["account"] for rule in RULES}):
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "account.account",
            "search_read",
            [account_domain(models, db, uid, api_key, company_id, account_name)],
            {"fields": ["id", "name"], "limit": 2},
        )
        if len(rows) != 1:
            raise SystemExit(f"Expected one account named {account_name}; found {len(rows)}.")
        accounts[account_name] = rows[0]["id"]
    return accounts


def next_day(day_text):
    return (datetime.strptime(day_text, "%Y-%m-%d").date() + timedelta(days=1)).isoformat()


def bank_lines(models, db, uid, api_key, company_id, journal_id, day_text):
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
            ("date", ">=", day_text),
            ("date", "<", next_day(day_text)),
        ]],
        {
            "fields": ["id", "date", "amount", "payment_ref", "is_reconciled", "move_id", "partner_id"],
            "limit": 5000,
            "order": "date asc,id asc",
        },
    )


def read_move_lines(models, db, uid, api_key, move_ids):
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
                "account_id",
                "date",
                "name",
                "ref",
                "debit",
                "credit",
                "balance",
                "reconciled",
                "matching_number",
                "full_reconcile_id",
            ],
            "limit": 10000,
            "order": "move_id,id",
        },
    )


def snapshot_rows(lines, move_lines):
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line.get("move_id")), []).append(line)

    rows = []
    for bank in lines:
        rows_for_move = by_move.get(rel_id(bank.get("move_id")), [])
        if not rows_for_move:
            rows.append({
                "Bank Statement Line ID": bank["id"],
                "Bank Date": bank.get("date", ""),
                "Bank Amount": bank.get("amount", 0),
                "Bank Ref": bank.get("payment_ref", ""),
                "Bank Partner": rel_name(bank.get("partner_id")),
                "Bank Is Reconciled": bank.get("is_reconciled", False),
                "Bank Move": rel_name(bank.get("move_id")),
                "Move Line ID": "",
                "Account": "",
                "Debit": "",
                "Credit": "",
                "Balance": "",
                "Move Line Reconciled": "",
                "Matching Number": "",
                "Full Reconcile": "",
            })
            continue
        for line in rows_for_move:
            rows.append({
                "Bank Statement Line ID": bank["id"],
                "Bank Date": bank.get("date", ""),
                "Bank Amount": bank.get("amount", 0),
                "Bank Ref": bank.get("payment_ref", ""),
                "Bank Partner": rel_name(bank.get("partner_id")),
                "Bank Is Reconciled": bank.get("is_reconciled", False),
                "Bank Move": rel_name(bank.get("move_id")),
                "Move Line ID": line["id"],
                "Account": rel_name(line.get("account_id")),
                "Debit": line.get("debit", 0),
                "Credit": line.get("credit", 0),
                "Balance": line.get("balance", 0),
                "Move Line Reconciled": line.get("reconciled", False),
                "Matching Number": line.get("matching_number", ""),
                "Full Reconcile": rel_name(line.get("full_reconcile_id")),
            })
    return rows


def blocked_reason(ref):
    for pattern in BLOCKED_REVIEW_PATTERNS:
        if re.search(pattern, ref or "", re.I):
            return "Protected review bucket: customer deposit, Shop Boss/card batch, check, loan, card payment, or transfer."
    return ""


def matched_rule(ref, amount):
    for rule in RULES:
        if rule.get("negative_only") and amount >= 0:
            continue
        if any(re.search(pattern, ref or "", re.I) for pattern in rule["patterns"]):
            return rule
    return None


def build_plan(lines, move_lines):
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line.get("move_id")), []).append(line)

    plan = []
    for bank in lines:
        ref = bank.get("payment_ref") or ""
        amount = float(bank.get("amount") or 0)
        base = {
            "Status": "",
            "Action": "",
            "Bank Statement Line ID": bank["id"],
            "Date": bank.get("date", ""),
            "Amount": amount,
            "Payment Ref": ref,
            "Current Reconciled": bank.get("is_reconciled", False),
            "New Account": "",
            "Suspense Move Line ID": "",
            "After Reconciled": "",
            "Reason": "",
        }
        if bank.get("is_reconciled"):
            plan.append({**base, "Status": "Skipped", "Action": "none", "Reason": "Already reconciled before daily agent run."})
            continue

        rule = matched_rule(ref, amount)
        if not rule:
            reason = blocked_reason(ref) or "No conservative daily auto-reconcile rule matched."
            plan.append({**base, "Status": "Review", "Action": "none", "Reason": reason})
            continue

        suspense = [
            line for line in by_move.get(rel_id(bank.get("move_id")), [])
            if rel_name(line.get("account_id")) == "Bank Suspense Account"
        ]
        if len(suspense) != 1:
            plan.append({
                **base,
                "Status": "Review",
                "Action": "none",
                "New Account": rule["account"],
                "Reason": f"Expected exactly one Bank Suspense Account line; found {len(suspense)}.",
            })
            continue

        plan.append({
            **base,
            "Status": "Ready",
            "Action": "apply_daily_safe_reference_rule",
            "New Account": rule["account"],
            "Suspense Move Line ID": suspense[0]["id"],
            "Reason": "Matched a conservative daily reference rule.",
        })
    return plan


def apply_plan(models, db, uid, api_key, accounts, plan):
    results = []
    for row in plan:
        if row["Status"] != "Ready":
            results.append(row)
            continue
        line_id = int(row["Suspense Move Line ID"])
        account_id = accounts[row["New Account"]]
        execute(models, db, uid, api_key, "account.move.line", "write", [[line_id], {"account_id": account_id}])
        after = execute(
            models,
            db,
            uid,
            api_key,
            "account.bank.statement.line",
            "read",
            [[int(row["Bank Statement Line ID"])]],
            {"fields": ["is_reconciled"]},
        )[0]
        results.append({
            **row,
            "Status": "Reconciled" if after["is_reconciled"] else "Review",
            "After Reconciled": after["is_reconciled"],
            "Reason": "Applied a conservative daily reference rule." if after["is_reconciled"] else "Account was changed, but Odoo did not mark the bank line reconciled.",
        })
    return results


def write_summary(out_dir, day_text, apply, rows):
    counts = Counter(row["Status"] for row in rows)
    total_ready_amount = sum(float(row.get("Amount") or 0) for row in rows if row.get("Status") in {"Ready", "Reconciled"})
    summary = out_dir / "daily_auto_reconcile_summary.md"
    summary.write_text(
        f"""# Daily Odoo Auto Reconcile Agent

Date: {day_text}

Odoo write performed: {'yes' if apply else 'no, dry run only'}

## Results

- Total bank lines reviewed: {len(rows)}
- Ready/Reconciled safe-rule lines: {counts.get('Ready', 0) + counts.get('Reconciled', 0)}
- Review lines left untouched: {counts.get('Review', 0)}
- Already reconciled lines skipped: {counts.get('Skipped', 0)}
- Net amount covered by safe rules: ${total_ready_amount:,.2f}

## Guardrails

- Customer/card deposits and Shop Boss/card batches are review-only.
- Checks are review-only unless a later script adds check-register evidence.
- Loan transfers, card payments, and ambiguous transfers are review-only.
- The agent only changes Bank Suspense Account move lines on Laurel bank statement items.
""",
        encoding="utf-8",
    )
    return summary


def main():
    parser = argparse.ArgumentParser(description="Daily guarded Odoo bank auto-reconciliation agent.")
    parser.add_argument("--date", default=date.today().isoformat(), help="Bank statement date to process in YYYY-MM-DD format. Default: today.")
    parser.add_argument("--apply", action="store_true", help="Apply safe-rule reconciliations. Default is dry run only.")
    args = parser.parse_args()

    datetime.strptime(args.date, "%Y-%m-%d")
    out_dir = OUT_ROOT / args.date
    fields = [
        "Status",
        "Action",
        "Bank Statement Line ID",
        "Date",
        "Amount",
        "Payment Ref",
        "Current Reconciled",
        "New Account",
        "Suspense Move Line ID",
        "After Reconciled",
        "Reason",
    ]

    db, uid, api_key, models = connect()
    company, journal = get_company_journal(models, db, uid, api_key)
    accounts = load_accounts(models, db, uid, api_key, company["id"])
    lines = bank_lines(models, db, uid, api_key, company["id"], journal["id"], args.date)
    move_ids = [rel_id(line.get("move_id")) for line in lines if rel_id(line.get("move_id"))]
    move_lines = read_move_lines(models, db, uid, api_key, move_ids)

    snapshot = snapshot_rows(lines, move_lines)
    write_csv(
        out_dir / "daily_auto_reconcile_snapshot_before.csv",
        snapshot,
        [
            "Bank Statement Line ID",
            "Bank Date",
            "Bank Amount",
            "Bank Ref",
            "Bank Partner",
            "Bank Is Reconciled",
            "Bank Move",
            "Move Line ID",
            "Account",
            "Debit",
            "Credit",
            "Balance",
            "Move Line Reconciled",
            "Matching Number",
            "Full Reconcile",
        ],
    )

    plan = build_plan(lines, move_lines)
    write_csv(out_dir / "daily_auto_reconcile_plan.csv", plan, fields)
    results = apply_plan(models, db, uid, api_key, accounts, plan) if args.apply else plan
    write_csv(out_dir / "daily_auto_reconcile_results.csv", results, fields)
    summary = write_summary(out_dir, args.date, args.apply, results)

    counts = Counter(row["Status"] for row in results)
    print(f"Connected uid: {uid}")
    print(f"Date: {args.date}")
    print(f"Applied: {args.apply}")
    print(f"Bank lines reviewed: {len(lines)}")
    print(f"Status counts: {dict(counts)}")
    print(f"Output folder: {out_dir}")
    print(f"Summary: {summary}")


if __name__ == "__main__":
    main()
