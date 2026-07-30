import csv
import os
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
PLAN = OUT / "odoo_shop_boss_split_deposits_apply_plan.csv"

TARGET_COMPANY_NAME = "Southern Equipment Company (Laurel)"
TARGET_JOURNAL_NAME = "Bank"

APPROVED_SPLITS = {
    384: {
        "source": "Part Sales 331",
        "parts_revenue": Decimal("144.96"),
        "service_revenue": Decimal("0.00"),
        "sales_tax": Decimal("10.15"),
        "merchant_fee": Decimal("0.00"),
    },
    411: {
        "source": "Part Sales 314",
        "parts_revenue": Decimal("2090.01"),
        "service_revenue": Decimal("0.00"),
        "sales_tax": Decimal("146.30"),
        "merchant_fee": Decimal("0.00"),
    },
    372: {
        "source": "Part Sales 316; Part Sales 320; Part Sales 323",
        "parts_revenue": Decimal("190.99"),
        "service_revenue": Decimal("0.00"),
        "sales_tax": Decimal("0.00"),
        # Use actual fee required to bridge gross to bank deposit.
        "merchant_fee": Decimal("6.20"),
    },
}

ACCOUNT_NAMES = {
    "parts_revenue": "Parts Revenue",
    "service_revenue": "Service Revenue",
    "sales_tax": "Sales Tax Payable",
    "merchant_fee": "Bank Merchant Fees",
}


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read(models, db, uid, api_key, model, domain, fields, limit=10000, order=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def rel_id(value):
    if isinstance(value, list) and value:
        return value[0]
    return False


def rel_name(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def single(models, db, uid, api_key, model, domain, fields, label):
    rows = read(models, db, uid, api_key, model, domain, fields, limit=2)
    if len(rows) != 1:
        raise SystemExit(f"Expected one {label}; found {len(rows)}")
    return rows[0]


def account_domain(models, db, uid, api_key, company_id, account_name):
    fields = execute(models, db, uid, api_key, "account.account", "fields_get", [], {"attributes": ["type"]})
    domain = [("name", "=", account_name)]
    if "company_ids" in fields:
        domain.append(("company_ids", "in", [company_id]))
    elif "company_id" in fields:
        domain.append(("company_id", "=", company_id))
    return domain


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

    company = single(models, db, uid, api_key, "res.company", [("name", "=", TARGET_COMPANY_NAME)], ["id", "name"], TARGET_COMPANY_NAME)
    journal = single(models, db, uid, api_key, "account.journal", [("name", "=", TARGET_JOURNAL_NAME), ("company_id", "=", company["id"])], ["id", "name"], TARGET_JOURNAL_NAME)
    accounts = {
        key: single(models, db, uid, api_key, "account.account", account_domain(models, db, uid, api_key, company["id"], name), ["id", "name", "code"], name)
        for key, name in ACCOUNT_NAMES.items()
    }

    bank_ids = list(APPROVED_SPLITS.keys())
    bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [("id", "in", bank_ids), ("company_id", "=", company["id"]), ("journal_id", "=", journal["id"]), ("is_reconciled", "=", False)],
        ["id", "date", "payment_ref", "amount", "move_id"],
        limit=100,
        order="date asc,id asc",
    )
    by_bank_id = {row["id"]: row for row in bank_lines}
    move_ids = [rel_id(row.get("move_id")) for row in bank_lines if rel_id(row.get("move_id"))]
    move_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        [("move_id", "in", move_ids)],
        ["id", "move_id", "account_id", "debit", "credit", "balance", "name"],
        limit=1000,
        order="move_id,id",
    )
    by_move = {}
    for line in move_lines:
        by_move.setdefault(rel_id(line["move_id"]), []).append(line)

    rows = []
    actions = []
    for bank_id, split in APPROVED_SPLITS.items():
        bank = by_bank_id.get(bank_id)
        if not bank:
            continue
        move_id = rel_id(bank["move_id"])
        lines = by_move.get(move_id, [])
        suspense = [line for line in lines if rel_name(line.get("account_id")) == "Bank Suspense Account"]
        already_split = [line for line in lines if rel_name(line.get("account_id")) in {a["name"] for a in accounts.values()}]
        if len(suspense) != 1 or already_split:
            rows.append({"Bank Line ID": bank_id, "Status": "Skipped", "Reason": "No single suspense line or split lines already present"})
            continue
        gross_credit = split["parts_revenue"] + split["service_revenue"] + split["sales_tax"]
        required_debits = money(bank["amount"]) + split["merchant_fee"]
        if gross_credit != required_debits:
            rows.append({"Bank Line ID": bank_id, "Status": "Skipped", "Reason": f"Split does not balance: credits {gross_credit} debits {required_debits}"})
            continue

        source_label = f"Shop Boss {split['source']}"
        update_values = {
            "account_id": accounts["parts_revenue"]["id"],
            "name": source_label,
            "debit": 0.0,
            "credit": float(split["parts_revenue"]),
        }
        create_values = []
        if split["service_revenue"]:
            create_values.append({"move_id": move_id, "account_id": accounts["service_revenue"]["id"], "name": source_label, "debit": 0.0, "credit": float(split["service_revenue"])})
        if split["sales_tax"]:
            create_values.append({"move_id": move_id, "account_id": accounts["sales_tax"]["id"], "name": source_label, "debit": 0.0, "credit": float(split["sales_tax"])})
        if split["merchant_fee"]:
            create_values.append({"move_id": move_id, "account_id": accounts["merchant_fee"]["id"], "name": source_label, "debit": float(split["merchant_fee"]), "credit": 0.0})

        rows.append(
            {
                "Bank Line ID": bank_id,
                "Status": "Ready",
                "Date": bank["date"],
                "Payment Ref": bank["payment_ref"],
                "Bank Amount": bank["amount"],
                "Source": split["source"],
                "Parts Revenue": float(split["parts_revenue"]),
                "Sales Tax Payable": float(split["sales_tax"]),
                "Bank Merchant Fees": float(split["merchant_fee"]),
                "Suspense Move Line ID": suspense[0]["id"],
                "New Lines": len(create_values),
                "Reason": "",
            }
        )
        actions.append((suspense[0]["id"], update_values, create_values))

    fieldnames = [
        "Bank Line ID", "Status", "Date", "Payment Ref", "Bank Amount", "Source",
        "Parts Revenue", "Sales Tax Payable", "Bank Merchant Fees",
        "Suspense Move Line ID", "New Lines", "Reason",
    ]
    with PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    if apply:
        context = {"check_move_validity": False}
        for suspense_line_id, update_values, create_values in actions:
            execute(models, db, uid, api_key, "account.move.line", "write", [[suspense_line_id], update_values], {"context": context})
            for values in create_values:
                execute(models, db, uid, api_key, "account.move.line", "create", [values], {"context": context})

    print(f"Connected uid: {uid}")
    print(f"Ready actions: {len(actions)}")
    print(f"Applied: {apply}")
    print(f"Plan: {PLAN}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
