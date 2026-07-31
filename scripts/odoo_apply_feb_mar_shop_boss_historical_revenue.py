import csv
import os
import sys
import xmlrpc.client
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SOURCE = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "all_available_shop_boss_vs_odoo_revenue_by_month.csv"
OUT = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "feb_mar_shop_boss_historical_revenue_results.csv"
SUMMARY = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "feb_mar_shop_boss_historical_revenue_summary.md"

COMPANY_NAME = "Southern Equipment Company (Laurel)"
JOURNAL_NAME = "Miscellaneous Operations"
PARTS_ACCOUNT = "Parts Revenue"
SERVICE_ACCOUNT = "Service Revenue"
TAX_ACCOUNT = "Sales Tax Payable"
OFFSET_ACCOUNT = "Bank Suspense Account"
MONTHS = {"2026-02": "2026-02-28", "2026-03": "2026-03-31"}


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, key, model, method, args, kwargs or {})


def execute_void_ok(models, db, uid, key, model, method, args, kwargs=None):
    try:
        return execute(models, db, uid, key, model, method, args, kwargs)
    except xmlrpc.client.Fault as exc:
        if "cannot marshal None unless allow_none is enabled" in str(exc):
            return None
        raise


def money(value):
    return Decimal(str(value or "0").replace(",", "")).quantize(Decimal("0.01"))


def account(models, db, uid, key, company_id, name):
    rows = execute(
        models,
        db,
        uid,
        key,
        "account.account",
        "search_read",
        [[("company_ids", "in", [company_id]), ("name", "=", name)]],
        {"fields": ["id", "name"], "limit": 2},
    )
    if len(rows) != 1:
        raise SystemExit(f"Expected one account {name}, found {len(rows)}")
    return rows[0]


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
    journal_id = execute(
        models,
        db,
        uid,
        api_key,
        "account.journal",
        "search",
        [[("company_id", "=", company_id), ("name", "=", JOURNAL_NAME)]],
        {"limit": 1},
    )[0]
    accounts = {
        "parts": account(models, db, uid, api_key, company_id, PARTS_ACCOUNT),
        "service": account(models, db, uid, api_key, company_id, SERVICE_ACCOUNT),
        "tax": account(models, db, uid, api_key, company_id, TAX_ACCOUNT),
        "offset": account(models, db, uid, api_key, company_id, OFFSET_ACCOUNT),
    }
    source_rows = {row["month"]: row for row in csv.DictReader(SOURCE.open(newline="", encoding="utf-8"))}
    results = []
    for month, date in MONTHS.items():
        row = source_rows[month]
        parts = money(row["shop_boss_parts"])
        service = money(row["shop_boss_service"])
        tax = money(row["shop_boss_tax"])
        gross = parts + service + tax
        ref = f"Shop Boss historical paid sales revenue true-up {month}"
        existing = execute(
            models,
            db,
            uid,
            api_key,
            "account.move",
            "search_read",
            [[("company_id", "=", company_id), ("ref", "=", ref), ("state", "!=", "cancel")]],
            {"fields": ["id", "name", "state"], "limit": 5},
        )
        if existing:
            results.append(
                {
                    "month": month,
                    "date": date,
                    "move": existing[0]["name"],
                    "move_id": existing[0]["id"],
                    "parts": f"{float(parts):.2f}",
                    "service": f"{float(service):.2f}",
                    "tax": f"{float(tax):.2f}",
                    "gross_debit_bank_suspense": f"{float(gross):.2f}",
                    "status": "skipped_existing",
                }
            )
            continue
        lines = [
            (
                0,
                0,
                {
                    "name": f"{ref} - gross paid sales clearing",
                    "account_id": accounts["offset"]["id"],
                    "debit": float(gross),
                    "credit": 0.0,
                },
            ),
            (
                0,
                0,
                {
                    "name": f"{ref} - Shop Boss parts revenue",
                    "account_id": accounts["parts"]["id"],
                    "debit": 0.0,
                    "credit": float(parts),
                },
            ),
            (
                0,
                0,
                {
                    "name": f"{ref} - Shop Boss service revenue",
                    "account_id": accounts["service"]["id"],
                    "debit": 0.0,
                    "credit": float(service),
                },
            ),
        ]
        if tax:
            lines.append(
                (
                    0,
                    0,
                    {
                        "name": f"{ref} - Shop Boss sales tax",
                        "account_id": accounts["tax"]["id"],
                        "debit": 0.0,
                        "credit": float(tax),
                    },
                )
            )
        move_id = execute(
            models,
            db,
            uid,
            api_key,
            "account.move",
            "create",
            [
                {
                    "company_id": company_id,
                    "journal_id": journal_id,
                    "date": date,
                    "ref": ref,
                    "line_ids": lines,
                }
            ],
        )
        execute_void_ok(models, db, uid, api_key, "account.move", "action_post", [[move_id]])
        move = execute(models, db, uid, api_key, "account.move", "read", [[move_id]], {"fields": ["name"]})[0]
        results.append(
            {
                "month": month,
                "date": date,
                "move": move["name"],
                "move_id": move_id,
                "parts": f"{float(parts):.2f}",
                "service": f"{float(service):.2f}",
                "tax": f"{float(tax):.2f}",
                "gross_debit_bank_suspense": f"{float(gross):.2f}",
                "status": "posted",
            }
        )

    fields = ["month", "date", "move", "move_id", "parts", "service", "tax", "gross_debit_bank_suspense", "status"]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    posted = [row for row in results if row["status"] == "posted"]
    SUMMARY.write_text(
        "\n".join(
            [
                "# February/March Shop Boss Historical Revenue Applied",
                "",
                f"- Posted moves: {len(posted)}",
                f"- Parts revenue posted: ${sum(float(row['parts']) for row in posted):,.2f}",
                f"- Service revenue posted: ${sum(float(row['service']) for row in posted):,.2f}",
                f"- Sales tax payable posted: ${sum(float(row['tax']) for row in posted):,.2f}",
                f"- Bank Suspense debit posted: ${sum(float(row['gross_debit_bank_suspense']) for row in posted):,.2f}",
                "",
                "## Rows",
                "",
                *[
                    f"- {row['month']} -> {row['move']} / {row['status']} / revenue ${float(row['parts']) + float(row['service']):,.2f} / tax ${float(row['tax']):,.2f}"
                    for row in results
                ],
                "",
                "## Note",
                "",
                "Bank Suspense was used instead of Accounts Receivable so paid historical Shop Boss sales do not appear as open customer balances. The suspense balance should be cleared when opening bank/cash history is finalized.",
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
