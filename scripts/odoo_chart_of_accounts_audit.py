import argparse
import csv
import os
import re
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "accounting"
COMPANY = "Southern Equipment Company (Laurel)"

REQUIRED_DAILY_AGENT_ACCOUNTS = [
    "Bank Merchant Fees",
    "Interest Income",
    "Employer Payroll Taxes",
    "Sales Tax Payable",
    "Software Subscriptions",
    "Office Expenses",
    "Facility Expense",
    "Company Vehicle Expense",
    "Meals & Entertainment",
    "Marketing & Advertising",
    "Parts COGS",
    "Shop & Service Equipment",
]

RECOMMENDED_REVIEW_ACCOUNTS = [
    {
        "name": "Shop Boss Payment Clearing",
        "type_hint": "asset_current",
        "reason": "Temporary clearing account for Shop Boss payments before matching bank/card settlement batches.",
        "keywords": [r"shop boss.*clearing", r"payment clearing", r"undeposited"],
    },
    {
        "name": "Merchant Fee Clearing",
        "type_hint": "expense",
        "reason": "Separate merchant processing fees from gross Shop Boss/card deposit settlement differences.",
        "keywords": [r"merchant.*fee", r"card.*fee", r"bank merchant"],
    },
    {
        "name": "Checks Pending Payee Review",
        "type_hint": "expense",
        "reason": "Optional temporary review account for checks that have statement evidence but no payee detail yet.",
        "keywords": [r"checks pending", r"check.*review", r"uncategorized"],
    },
    {
        "name": "Loan Principal Payable",
        "type_hint": "liability_current",
        "reason": "Needed when bank loan drafts must be split between principal and interest.",
        "keywords": [r"loan.*principal", r"note payable", r"loan payable"],
    },
    {
        "name": "Loan Interest Expense",
        "type_hint": "expense",
        "reason": "Needed when bank loan drafts must be split between principal and interest.",
        "keywords": [r"interest expense", r"loan.*interest"],
    },
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


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) >= 2 else ""


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


def get_company(models, db, uid, api_key):
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
    return company[0]


def account_domain(account_fields, company_id):
    domain = []
    if "company_ids" in account_fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in account_fields:
        domain.append(("company_id", "=", company_id))
    return domain


def read_accounts(models, db, uid, api_key, company_id):
    account_fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    fields = ["id", "code", "name", "account_type"]
    for optional in ["deprecated", "company_id", "company_ids"]:
        if optional in account_fields:
            fields.append(optional)
    return execute(
        models,
        db,
        uid,
        api_key,
        "account.account",
        "search_read",
        [account_domain(account_fields, company_id)],
        {"fields": fields, "limit": 10000, "order": "code asc,name asc"},
    )


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def names_match(row, exact_name):
    return (row.get("name") or "").strip().lower() == exact_name.strip().lower()


def keyword_matches(accounts, patterns):
    matches = []
    for row in accounts:
        haystack = f"{row.get('code', '')} {row.get('name', '')}".strip()
        if any(re.search(pattern, haystack, re.I) for pattern in patterns):
            matches.append(row)
    return matches


def audit_accounts(accounts):
    rows = []
    for name in REQUIRED_DAILY_AGENT_ACCOUNTS:
        matches = [row for row in accounts if names_match(row, name)]
        rows.append({
            "Section": "Required by daily auto-reconcile agent",
            "Requested Account": name,
            "Status": "OK" if len(matches) == 1 else ("Missing" if not matches else "Review"),
            "Existing Matches": "; ".join(f"{row.get('code', '')} {row.get('name', '')} ({row.get('account_type', '')})" for row in matches),
            "Type Hint": "",
            "Reason": "The daily auto-reconcile agent writes only to accounts in this required list.",
        })

    for item in RECOMMENDED_REVIEW_ACCOUNTS:
        exact = [row for row in accounts if names_match(row, item["name"])]
        fuzzy = keyword_matches(accounts, item["keywords"])
        matches = exact or fuzzy
        rows.append({
            "Section": "Recommended for cleaner Shop Boss/bank workflows",
            "Requested Account": item["name"],
            "Status": "Exists/Similar" if matches else "Recommended",
            "Existing Matches": "; ".join(f"{row.get('code', '')} {row.get('name', '')} ({row.get('account_type', '')})" for row in matches[:6]),
            "Type Hint": item["type_hint"],
            "Reason": item["reason"],
        })
    return rows


def write_summary(path, rows):
    required_missing = [row for row in rows if row["Section"].startswith("Required") and row["Status"] != "OK"]
    recommended = [row for row in rows if row["Status"] == "Recommended"]
    lines = [
        "# Odoo Chart of Accounts Audit",
        "",
        f"Company: {COMPANY}",
        "",
        "## Result",
        "",
        f"- Required daily-agent accounts needing attention: {len(required_missing)}",
        f"- Recommended cleanup accounts not found: {len(recommended)}",
        "",
        "## Recommended Additions",
        "",
    ]
    if recommended:
        for row in recommended:
            lines.append(f"- {row['Requested Account']} ({row['Type Hint']}): {row['Reason']}")
    else:
        lines.append("- No recommended cleanup account gaps found.")
    lines.extend([
        "",
        "## Notes",
        "",
        "- This audit is read-only.",
        "- Do not add clearing accounts without confirming desired account codes and accountant preference.",
        "- Shop Boss remains the invoice and customer payment source of truth.",
    ])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Audit Odoo chart of accounts against Shop Boss/bank reconciliation needs.")
    parser.add_argument("--company", default=COMPANY, help="Reserved for future use. Current default is Laurel.")
    args = parser.parse_args()
    if args.company != COMPANY:
        raise SystemExit(f"Only {COMPANY!r} is supported right now.")

    db, uid, api_key, models = connect()
    company = get_company(models, db, uid, api_key)
    accounts = read_accounts(models, db, uid, api_key, company["id"])

    account_rows = [{
        "ID": row.get("id"),
        "Code": row.get("code", ""),
        "Name": row.get("name", ""),
        "Account Type": row.get("account_type", ""),
        "Company": rel_name(row.get("company_id")) or ",".join(str(value) for value in row.get("company_ids", [])),
        "Deprecated": row.get("deprecated", ""),
    } for row in accounts]
    audit_rows = audit_accounts(accounts)

    write_csv(
        OUT_DIR / "chart_of_accounts_laurel_export.csv",
        account_rows,
        ["ID", "Code", "Name", "Account Type", "Company", "Deprecated"],
    )
    write_csv(
        OUT_DIR / "chart_of_accounts_shop_boss_reconciliation_audit.csv",
        audit_rows,
        ["Section", "Requested Account", "Status", "Existing Matches", "Type Hint", "Reason"],
    )
    write_summary(OUT_DIR / "chart_of_accounts_shop_boss_reconciliation_audit.md", audit_rows)

    print(f"Connected uid: {uid}")
    print(f"Database: {db}")
    print(f"Company: {company['name']}")
    print(f"Accounts exported: {len(account_rows)}")
    print(f"Required gaps: {sum(1 for row in audit_rows if row['Section'].startswith('Required') and row['Status'] != 'OK')}")
    print(f"Recommended gaps: {sum(1 for row in audit_rows if row['Status'] == 'Recommended')}")
    print(f"Audit: {OUT_DIR / 'chart_of_accounts_shop_boss_reconciliation_audit.csv'}")


if __name__ == "__main__":
    main()
