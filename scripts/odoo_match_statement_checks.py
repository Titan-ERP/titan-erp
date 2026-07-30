import csv
import os
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
CHECKS = ROOT / "odoo_imports" / "bank_reconciliation" / "check_details_from_statements.csv"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
MATCHES = OUT / "bank_check_detail_matches.csv"
TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def cents(value):
    return int(Decimal(str(value or "0").replace(",", "")).quantize(Decimal("0.01")) * 100)


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def main():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", TARGET_COMPANY_NAME)]], {"fields": ["id"], "limit": 1})[0]
    journal = execute(models, db, uid, api_key, "account.journal", "search_read", [[("name", "=", TARGET_JOURNAL_NAME), ("company_id", "=", company["id"])]], {"fields": ["id"], "limit": 1})[0]
    bank_lines = execute(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        "search_read",
        [[("company_id", "=", company["id"]), ("journal_id", "=", journal["id"]), ("is_reconciled", "=", False)]],
        {"fields": ["id", "date", "payment_ref", "amount", "partner_id", "move_id"], "limit": 20000, "order": "date asc"},
    )

    by_date_amount = defaultdict(list)
    for line in bank_lines:
        by_date_amount[(line["date"], abs(cents(line["amount"])))].append(line)

    rows = []
    matched = 0
    for check in read_csv(CHECKS):
        key = (check["Date"], cents(check["Amount"]))
        candidates = by_date_amount.get(key, [])
        status = "No Odoo match"
        line = {}
        if len(candidates) == 1:
            status = "Matched"
            line = candidates[0]
            matched += 1
        elif len(candidates) > 1:
            check_like = [row for row in candidates if "CHECK" in str(row.get("payment_ref", "")).upper()]
            if len(check_like) == 1:
                status = "Matched"
                line = check_like[0]
                matched += 1
            else:
                status = f"Ambiguous ({len(candidates)} candidates)"
        rows.append(
            {
                "Status": status,
                "Bank Statement Line ID": line.get("id", ""),
                "Odoo Date": line.get("date", ""),
                "Odoo Amount": line.get("amount", ""),
                "Odoo Ref": line.get("payment_ref", ""),
                "Odoo Partner": rel(line.get("partner_id")),
                "Check Number": check["Check Number"],
                "Check Date": check["Date"],
                "Check Amount": check["Amount"],
                "Payee": check["Payee"],
                "Memo": check["Memo"],
                "Suggested Ref": f"Check {check['Check Number']} - {check['Payee']}".strip(),
                "Source Page": check["Source Page"],
                "Confidence": check["Confidence"],
            }
        )

    fields = [
        "Status",
        "Bank Statement Line ID",
        "Odoo Date",
        "Odoo Amount",
        "Odoo Ref",
        "Odoo Partner",
        "Check Number",
        "Check Date",
        "Check Amount",
        "Payee",
        "Memo",
        "Suggested Ref",
        "Source Page",
        "Confidence",
    ]
    with MATCHES.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Connected uid: {uid}")
    print(f"Statement checks loaded: {len(rows)}")
    print(f"Matched to live Laurel Bank unreconciled lines: {matched}")
    print(f"Match file: {MATCHES}")


if __name__ == "__main__":
    main()
