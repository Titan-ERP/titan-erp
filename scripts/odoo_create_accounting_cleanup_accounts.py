import csv
import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "accounting_cleanup_account_creation_results.csv"
SUMMARY = ROOT / "odoo_imports" / "accounting" / "accounting_cleanup_account_creation_summary.md"
COMPANY_NAME = "Southern Equipment Company (Laurel)"

ACCOUNTS = [
    {
        "name": "Shop Boss Payment Clearing",
        "account_type": "asset_current",
        "code": "109998",
        "reason": "Clears Shop Boss gross payments/card batches before matching net bank deposits and merchant fees.",
    },
    {
        "name": "Checks Pending Payee Review",
        "account_type": "expense",
        "code": "699998",
        "reason": "Temporary review account for checks where the bank statement only says Check and no payee detail is available yet.",
    },
]


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, key, model, method, args, kwargs or {})


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
    fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type", "required"]})

    results = []
    for item in ACCOUNTS:
        existing = execute(
            models,
            db,
            uid,
            api_key,
            "account.account",
            "search_read",
            [[("name", "=", item["name"]), ("company_ids", "in", [company_id])]],
            {"fields": ["id", "name", "account_type"], "limit": 5},
        )
        if existing:
            results.append(
                {
                    "account": item["name"],
                    "account_type": item["account_type"],
                    "account_id": existing[0]["id"],
                    "status": "already_exists",
                    "reason": item["reason"],
                }
            )
            continue
        vals = {"name": item["name"], "account_type": item["account_type"]}
        if "company_ids" in fields:
            vals["company_ids"] = [(6, 0, [company_id])]
        elif "company_id" in fields:
            vals["company_id"] = company_id
        vals["code"] = item["code"]
        account_id = execute(models, db, uid, api_key, "account.account", "create", [vals])
        results.append(
            {
                "account": item["name"],
                "account_type": item["account_type"],
                "account_id": account_id,
                "status": "created",
                "reason": item["reason"],
            }
        )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = ["account", "account_type", "account_id", "status", "reason"]
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    created = [row for row in results if row["status"] == "created"]
    SUMMARY.write_text(
        "\n".join(
            [
                "# Accounting Cleanup Accounts",
                "",
                f"- Created accounts: {len(created)}",
                f"- Existing/unchanged accounts: {len(results) - len(created)}",
                "",
                "## Rows",
                "",
                *[
                    f"- {row['account']} ({row['account_type']}): {row['status']} / id {row['account_id']}"
                    for row in results
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    print(SUMMARY)


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
