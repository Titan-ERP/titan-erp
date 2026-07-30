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
JOURNAL = "Bank"

EXCLUDED_STABLE_ACCOUNTS = {
    "Operating Checking - SEC Laurel",
    "Bank Suspense Account",
    "Parts Revenue",
    "Service Revenue",
    "Sales Tax Payable",
    "Accounts Receivable",
}

SAFE_RULES = [
    ("Bank Merchant Fees", [r"MONTHLY DEBIT CARD FEE", r"BANKCARD-1205/MTOT DEP"]),
    ("Sales Tax Payable", [r"IRS/USATAXPYMT", r"MSDEPTOFREVENUE/TAXPAYMENT"]),
    ("Parts COGS", [r"FRIDAYPARTS", r"COLE TRACTOR", r"SQ \*WEST VIRGINIA MANUFAC"]),
    ("Software Subscriptions", [r"VONAGE BUSINESS", r"WWW\.SMALINK\.COM"]),
    ("Office Expenses", [r"WAL WAL-MART", r"USPS PO"]),
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


def execute_void_ok(models, db, uid, api_key, model, method, args, kwargs=None):
    try:
        return execute(models, db, uid, api_key, model, method, args, kwargs)
    except xmlrpc.client.Fault as exc:
        if "cannot marshal None unless allow_none is enabled" in str(exc):
            return None
        raise


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


def account_for_ref(ref):
    for account, patterns in SAFE_RULES:
        if any(re.search(pattern, ref or "", re.I) for pattern in patterns):
            return account
    return ""


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def get_company_journal(models, db, uid, api_key):
    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})[0]
    journal = execute(
        models, db, uid, api_key, "account.journal", "search_read",
        [[("name", "=", JOURNAL), ("company_id", "=", company["id"])]],
        {"fields": ["id"], "limit": 1},
    )[0]
    return company, journal


def bank_lines(models, db, uid, api_key, company_id, journal_id, date_from, date_to, reconciled=None):
    domain = [
        ("company_id", "=", company_id),
        ("journal_id", "=", journal_id),
        ("date", ">=", date_from),
        ("date", "<", date_to),
    ]
    if reconciled is not None:
        domain.append(("is_reconciled", "=", reconciled))
    return execute(
        models, db, uid, api_key, "account.bank.statement.line", "search_read",
        [domain],
        {"fields": ["id", "date", "amount", "payment_ref", "is_reconciled", "move_id", "partner_id"], "limit": 50000, "order": "date asc,id asc"},
    )


def read_move_lines(models, db, uid, api_key, move_ids):
    if not move_ids:
        return []
    return execute(
        models, db, uid, api_key, "account.move.line", "search_read",
        [[("move_id", "in", move_ids)]],
        {
            "fields": [
                "id", "move_id", "date", "name", "ref", "partner_id", "account_id",
                "debit", "credit", "balance", "amount_residual", "reconciled",
                "matching_number", "full_reconcile_id",
            ],
            "limit": 100000,
            "order": "move_id,id",
        },
    )


def snapshot(models, db, uid, api_key, lines, path):
    move_ids = [rel_id(row.get("move_id")) for row in lines if rel_id(row.get("move_id"))]
    move_lines = read_move_lines(models, db, uid, api_key, move_ids)
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line.get("move_id")), []).append(line)
    rows = []
    for bank in lines:
        for line in by_move.get(rel_id(bank.get("move_id")), []):
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
                "Residual": line.get("amount_residual", 0),
                "Move Line Reconciled": line.get("reconciled", False),
                "Matching Number": line.get("matching_number", ""),
                "Full Reconcile": rel_name(line.get("full_reconcile_id")),
            })
    fields = list(rows[0].keys()) if rows else [
        "Bank Statement Line ID", "Bank Date", "Bank Amount", "Bank Ref", "Bank Partner",
        "Bank Is Reconciled", "Bank Move", "Move Line ID", "Account", "Debit", "Credit",
        "Balance", "Residual", "Move Line Reconciled", "Matching Number", "Full Reconcile",
    ]
    write_csv(path, rows, fields)
    return rows


def unreconcile(models, db, uid, api_key, lines, path, apply):
    rows = []
    ids = [line["id"] for line in lines if line.get("is_reconciled")]
    if apply and ids:
        method = "button_undo_reconciliation"
        try:
            execute_void_ok(models, db, uid, api_key, "account.bank.statement.line", method, [ids])
        except Exception:
            method = "action_undo_reconciliation"
            execute_void_ok(models, db, uid, api_key, "account.bank.statement.line", method, [ids])
        after = execute(models, db, uid, api_key, "account.bank.statement.line", "read", [ids], {"fields": ["id", "is_reconciled"]})
        after_by_id = {row["id"]: row["is_reconciled"] for row in after}
    else:
        method = "action_undo_reconciliation"
        after_by_id = {}
    for line in lines:
        if not line.get("is_reconciled"):
            continue
        after_state = after_by_id.get(line["id"], "")
        rows.append({
            "Status": "Unreconciled" if apply and not after_state else ("Ready" if not apply else "Review"),
            "Bank Statement Line ID": line["id"],
            "Date": line.get("date", ""),
            "Amount": line.get("amount", 0),
            "Payment Ref": line.get("payment_ref", ""),
            "Before Reconciled": line.get("is_reconciled"),
            "After Reconciled": after_state,
            "Method": method,
        })
    write_csv(path, rows, ["Status", "Bank Statement Line ID", "Date", "Amount", "Payment Ref", "Before Reconciled", "After Reconciled", "Method"])
    return rows


def rebuild(models, db, uid, api_key, company_id, date_from, date_to, snapshot_rows, path, apply):
    stable_source = [
        row for row in snapshot_rows
        if str(row["Bank Is Reconciled"]) == "True"
        and row["Account"] not in EXCLUDED_STABLE_ACCOUNTS
        and not (row["Account"] == "Bank Merchant Fees" and float(row["Bank Amount"]) > 0)
    ]
    by_bank = {}
    for row in stable_source:
        by_bank.setdefault(str(row["Bank Statement Line ID"]), []).append(row)
    stable_plan = [rows[0] for rows in by_bank.values() if len(rows) == 1 and len({row["Account"] for row in rows}) == 1]

    account_names = sorted({row["Account"] for row in stable_plan} | {name for name, _ in SAFE_RULES})
    accounts = {}
    for name in account_names:
        found = execute(
            models, db, uid, api_key, "account.account", "search_read",
            [account_domain(models, db, uid, api_key, company_id, name)],
            {"fields": ["id", "name"], "limit": 2},
        )
        if len(found) == 1:
            accounts[name] = found[0]["id"]

    all_lines = bank_lines(models, db, uid, api_key, company_id, JOURNAL_ID, date_from, date_to)
    current_by_id = {str(row["id"]): row for row in all_lines}
    move_ids = [rel_id(row.get("move_id")) for row in all_lines if rel_id(row.get("move_id"))]
    move_lines = read_move_lines(models, db, uid, api_key, move_ids)
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    rebuild_rows = []
    used = set()
    candidates = []
    for source in stable_plan:
        bank = current_by_id.get(str(source["Bank Statement Line ID"]))
        if bank and source["Account"] in accounts:
            candidates.append((bank, source["Account"], "restore_stable_counterpart_account", "Stable prior non-customer counterpart account."))
            used.add(str(bank["id"]))
    for bank in all_lines:
        if str(bank["id"]) in used:
            continue
        account = account_for_ref(bank.get("payment_ref") or "")
        if account and account in accounts:
            candidates.append((bank, account, "apply_safe_reference_rule", "Conservative reference-based account rule."))

    for bank, account, action, reason in candidates:
        suspense = [
            line for line in by_move.get(rel_id(bank["move_id"]), [])
            if rel_name(line.get("account_id")) == "Bank Suspense Account"
        ]
        base = {
            "Bank Statement Line ID": bank["id"],
            "Date": bank.get("date", ""),
            "Amount": bank.get("amount", 0),
            "Payment Ref": bank.get("payment_ref", ""),
            "New Account": account,
            "Action": action,
        }
        if len(suspense) != 1:
            rebuild_rows.append({**base, "Status": "Review", "Reason": f"Expected one suspense line; found {len(suspense)}."})
            continue
        if apply and bank.get("is_reconciled"):
            rebuild_rows.append({**base, "Status": "Skipped", "Reason": "Bank line remained reconciled after unreconcile step."})
            continue
        base["Suspense Move Line ID"] = suspense[0]["id"]
        if apply:
            execute(models, db, uid, api_key, "account.move.line", "write", [[suspense[0]["id"]], {"account_id": accounts[account]}])
            after = execute(models, db, uid, api_key, "account.bank.statement.line", "read", [[bank["id"]]], {"fields": ["is_reconciled"]})[0]
            rebuild_rows.append({**base, "Status": "Rebuilt" if after["is_reconciled"] else "Review", "After Reconciled": after["is_reconciled"], "Reason": reason})
        else:
            rebuild_rows.append({**base, "Status": "Ready", "After Reconciled": "", "Reason": reason})
    write_csv(path, rebuild_rows, ["Status", "Action", "Bank Statement Line ID", "Date", "Amount", "Payment Ref", "New Account", "Suspense Move Line ID", "After Reconciled", "Reason"])
    return rebuild_rows


def export_final(models, db, uid, api_key, company_id, journal_id, date_from, date_to, path):
    lines = bank_lines(models, db, uid, api_key, company_id, journal_id, date_from, date_to)
    rows = [{
        "Bank Statement Line ID": row["id"],
        "Date": row.get("date", ""),
        "Amount": row.get("amount", 0),
        "Payment Ref": row.get("payment_ref", ""),
        "Is Reconciled": row.get("is_reconciled", False),
    } for row in lines]
    write_csv(path, rows, ["Bank Statement Line ID", "Date", "Amount", "Payment Ref", "Is Reconciled"])
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-from", default="2026-01-01")
    parser.add_argument("--date-to", default="2026-08-01")
    parser.add_argument("--label", default="2026_ytd")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    global JOURNAL_ID
    db, uid, api_key, models = connect()
    company, journal = get_company_journal(models, db, uid, api_key)
    JOURNAL_ID = journal["id"]
    prefix = OUT_DIR / f"bank_rebuild_{args.label}"

    before_lines = bank_lines(models, db, uid, api_key, company["id"], journal["id"], args.date_from, args.date_to)
    snapshot_rows = snapshot(models, db, uid, api_key, before_lines, prefix.with_name(prefix.name + "_snapshot_before.csv"))
    unreconcile_rows = unreconcile(models, db, uid, api_key, before_lines, prefix.with_name(prefix.name + "_unreconcile_results.csv"), args.apply)
    rebuild_rows = rebuild(models, db, uid, api_key, company["id"], args.date_from, args.date_to, snapshot_rows, prefix.with_name(prefix.name + "_rebuild_results.csv"), args.apply)
    final_rows = export_final(models, db, uid, api_key, company["id"], journal["id"], args.date_from, args.date_to, prefix.with_name(prefix.name + "_final_bank_lines.csv"))

    before_reconciled = sum(1 for row in before_lines if row.get("is_reconciled"))
    final_reconciled = sum(1 for row in final_rows if str(row["Is Reconciled"]).lower() == "true")
    print(f"Connected uid: {uid}")
    print(f"Range: {args.date_from} to {args.date_to} exclusive")
    print(f"Applied: {args.apply}")
    print(f"Bank lines: {len(before_lines)}")
    print(f"Reconciled before: {before_reconciled}")
    print(f"Unreconcile rows: {len(unreconcile_rows)}")
    print(f"Rebuild rows: {len(rebuild_rows)}")
    print(f"Reconciled after: {final_reconciled}")
    print(f"Outputs: {prefix.with_name(prefix.name + '_*.csv')}")


if __name__ == "__main__":
    main()
