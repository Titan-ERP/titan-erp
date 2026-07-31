import csv
import os
import sys
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26"
DETAIL = OUT_DIR / "bank_posted_revenue_vs_shop_boss_gaps.csv"
SUMMARY = OUT_DIR / "bank_posted_revenue_vs_shop_boss_gaps.md"
COMPANY_NAME = "Southern Equipment Company (Laurel)"


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, key, model, method, args, kwargs or {})


def money(value):
    return Decimal(str(value or "0").replace(",", "")).quantize(Decimal("0.01"))


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

    revenue_accounts = execute(
        models,
        db,
        uid,
        api_key,
        "account.account",
        "search_read",
        [[("company_ids", "in", [company_id]), ("name", "in", ["Parts Revenue", "Service Revenue", "Rental Revenue"])]],
        {"fields": ["id", "name"]},
    )
    account_ids = [row["id"] for row in revenue_accounts]
    lines = execute(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        "search_read",
        [
            [
                ("company_id", "=", company_id),
                ("parent_state", "=", "posted"),
                ("date", ">=", "2026-01-01"),
                ("date", "<", "2026-08-01"),
                ("account_id", "in", account_ids),
            ]
        ],
        {
            "fields": ["date", "account_id", "balance", "move_id", "journal_id", "ref", "name", "partner_id"],
            "limit": 20000,
            "order": "date asc,id asc",
        },
    )
    bank_posted = defaultdict(lambda: defaultdict(Decimal))
    invoice_posted = defaultdict(lambda: defaultdict(Decimal))
    misc_posted = defaultdict(lambda: defaultdict(Decimal))
    detail = []
    move_ids = list({line["move_id"][0] for line in lines if isinstance(line.get("move_id"), list)})
    moves = {}
    for i in range(0, len(move_ids), 200):
        chunk = move_ids[i : i + 200]
        for row in execute(
            models,
            db,
            uid,
            api_key,
            "account.move",
            "read",
            [chunk],
            {"fields": ["id", "name", "move_type", "journal_id", "ref", "invoice_origin"]},
        ):
            moves[row["id"]] = row
    for line in lines:
        move = moves.get(line["move_id"][0], {})
        month = str(line["date"])[:7]
        account = line["account_id"][1]
        amount = money(-float(line["balance"]))
        journal = line["journal_id"][1] if isinstance(line.get("journal_id"), list) else ""
        move_type = move.get("move_type", "")
        if move_type == "out_invoice":
            bucket = "invoice_posted"
            invoice_posted[month][account] += amount
        elif journal.lower().startswith("bank") or str(move.get("name", "")).startswith("BNK"):
            bucket = "bank_posted"
            bank_posted[month][account] += amount
        else:
            bucket = "misc_posted"
            misc_posted[month][account] += amount
        detail.append(
            {
                "date": line["date"],
                "month": month,
                "bucket": bucket,
                "account": account,
                "amount": f"{float(amount):.2f}",
                "journal": journal,
                "move": move.get("name", ""),
                "move_type": move_type,
                "ref": move.get("ref") or line.get("ref") or "",
                "line_name": line.get("name") or "",
                "partner": line["partner_id"][1] if isinstance(line.get("partner_id"), list) else "",
            }
        )

    fields = ["date", "month", "bucket", "account", "amount", "journal", "move", "move_type", "ref", "line_name", "partner"]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with DETAIL.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(detail)

    months = sorted({row["month"] for row in detail})
    lines_out = ["# Bank Posted Revenue vs Shop Boss Gaps", ""]
    for month in months:
        bank_total = sum(bank_posted[month].values(), Decimal("0.00"))
        invoice_total = sum(invoice_posted[month].values(), Decimal("0.00"))
        misc_total = sum(misc_posted[month].values(), Decimal("0.00"))
        lines_out.append(
            f"- {month}: bank-posted revenue ${float(bank_total):,.2f}; invoice revenue ${float(invoice_total):,.2f}; misc true-up revenue ${float(misc_total):,.2f}"
        )
    lines_out.extend(["", "## Note", "", "Bank-posted revenue is the likely double-count risk if missing Shop Boss document invoices are created without reversing/reclassing the older bank simplification entries."])
    SUMMARY.write_text("\n".join(lines_out) + "\n", encoding="utf-8")
    print(SUMMARY)


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
