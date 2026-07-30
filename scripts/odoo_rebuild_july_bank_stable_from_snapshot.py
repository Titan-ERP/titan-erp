import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SNAPSHOT = ROOT / "odoo_imports" / "accounting" / "july_bank_reconciliation_snapshot_before_rebuild.csv"
OUT = ROOT / "odoo_imports" / "accounting" / "july_bank_stable_rebuild_results.csv"
COMPANY = "Southern Equipment Company (Laurel)"

EXCLUDED_ACCOUNTS = {
    "Operating Checking - SEC Laurel",
    "Bank Suspense Account",
    "Parts Revenue",
    "Service Revenue",
    "Sales Tax Payable",
    "Accounts Receivable",
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


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


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


def write_csv(rows):
    fields = [
        "Status", "Action", "Bank Statement Line ID", "Bank Date", "Bank Amount",
        "Bank Ref", "Account", "Suspense Move Line ID", "Before Reconciled",
        "After Reconciled", "Reason",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    apply = "--apply" in os.sys.argv
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY)]], {"fields": ["id"], "limit": 1})[0]
    snapshot_rows = [
        row for row in read_csv(SNAPSHOT)
        if row["Bank Is Reconciled"] == "True"
        and row["Account"] not in EXCLUDED_ACCOUNTS
        and not (row["Account"] == "Bank Merchant Fees" and float(row["Bank Amount"]) > 0)
    ]
    by_bank = {}
    for row in snapshot_rows:
        by_bank.setdefault(row["Bank Statement Line ID"], []).append(row)
    plan = [rows[0] for rows in by_bank.values() if len({row["Account"] for row in rows}) == 1 and len(rows) == 1]

    account_names = sorted({row["Account"] for row in plan})
    accounts = {}
    for name in account_names:
        found = execute(
            models, db, uid, api_key, "account.account", "search_read",
            [account_domain(models, db, uid, api_key, company["id"], name)],
            {"fields": ["id", "name", "code"], "limit": 2},
        )
        if len(found) != 1:
            raise SystemExit(f"Expected one account {name}; found {len(found)}")
        accounts[name] = found[0]["id"]

    bank_ids = [int(row["Bank Statement Line ID"]) for row in plan]
    bank_lines = execute(
        models, db, uid, api_key, "account.bank.statement.line", "read",
        [bank_ids],
        {"fields": ["id", "is_reconciled", "move_id"]},
    )
    bank_by_id = {row["id"]: row for row in bank_lines}
    move_ids = [rel_id(row["move_id"]) for row in bank_lines if rel_id(row.get("move_id"))]
    move_lines = execute(
        models, db, uid, api_key, "account.move.line", "search_read",
        [[("move_id", "in", move_ids)]],
        {"fields": ["id", "move_id", "account_id"], "limit": 50000},
    )
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    results = []
    writes = []
    for row in plan:
        bank_id = int(row["Bank Statement Line ID"])
        bank = bank_by_id[bank_id]
        suspense = [
            line for line in by_move.get(rel_id(bank["move_id"]), [])
            if rel_name(line.get("account_id")) == "Bank Suspense Account"
        ]
        result = {
            "Bank Statement Line ID": bank_id,
            "Bank Date": row["Bank Date"],
            "Bank Amount": row["Bank Amount"],
            "Bank Ref": row["Bank Ref"],
            "Account": row["Account"],
            "Before Reconciled": bank["is_reconciled"],
        }
        if bank["is_reconciled"]:
            results.append({**result, "Status": "Skipped", "Action": "none", "Reason": "Bank line is already reconciled."})
            continue
        if len(suspense) != 1:
            results.append({**result, "Status": "Review", "Action": "none", "Reason": f"Expected one suspense line; found {len(suspense)}."})
            continue
        result["Suspense Move Line ID"] = suspense[0]["id"]
        if apply:
            execute(models, db, uid, api_key, "account.move.line", "write", [[suspense[0]["id"]], {"account_id": accounts[row["Account"]]}])
            after = execute(models, db, uid, api_key, "account.bank.statement.line", "read", [[bank_id]], {"fields": ["is_reconciled"]})[0]
            results.append({
                **result,
                "Status": "Rebuilt" if after["is_reconciled"] else "Review",
                "Action": "restore_stable_counterpart_account",
                "After Reconciled": after["is_reconciled"],
                "Reason": "Restored stable prior non-customer counterpart account." if after["is_reconciled"] else "Account was written but bank line remains unreconciled.",
            })
        else:
            results.append({
                **result,
                "Status": "Ready",
                "Action": "restore_stable_counterpart_account",
                "After Reconciled": "",
                "Reason": "Would restore stable prior non-customer counterpart account.",
            })

    write_csv(results)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for result in results[:30]:
        print(result)


if __name__ == "__main__":
    main()
