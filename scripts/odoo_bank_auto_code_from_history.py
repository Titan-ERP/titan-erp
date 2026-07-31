import csv
import os
import re
import sys
import xmlrpc.client
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
PLAN = OUT / "odoo_bank_auto_code_from_history_plan.csv"


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env(ENV_PATH)
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


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


def merchant_key(ref):
    text = str(ref or "").upper()
    text = re.sub(r"\*+\d+", " ", text)
    text = re.sub(r"\b\d{2}/\d{2}\b.*$", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    text = re.sub(r"\b(POS PURCHASE|NON PIN|PIN|DEBIT|CARD|PURCHASE|CHECKCARD)\b", " ", text)
    text = re.sub(r"[^A-Z0-9&/# -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Keep enough to group chains without overfitting city/state noise.
    parts = text.split()
    return " ".join(parts[:5])


def get_move_lines(models, db, uid, api_key, move_ids):
    if not move_ids:
        return []
    return read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        [("move_id", "in", move_ids)],
        ["id", "move_id", "account_id", "partner_id", "debit", "credit", "balance", "name"],
        limit=max(10000, len(move_ids) * 5),
        order="id asc",
    )


def main():
    apply = "--apply" in sys.argv
    db, uid, api_key, models = connect()
    OUT.mkdir(parents=True, exist_ok=True)

    reconciled_bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [("date", ">=", "2026-03-01"), ("date", "<=", "2026-06-30"), ("is_reconciled", "=", True)],
        ["id", "date", "payment_ref", "amount", "move_id"],
        order="date asc",
    )
    rec_move_ids = [rel_id(line["move_id"]) for line in reconciled_bank_lines if rel_id(line.get("move_id"))]
    rec_move_lines = get_move_lines(models, db, uid, api_key, rec_move_ids)
    rec_by_move = defaultdict(list)
    for ml in rec_move_lines:
        rec_by_move[rel_id(ml["move_id"])].append(ml)

    mapping_counter = defaultdict(Counter)
    mapping_example = {}
    for bank in reconciled_bank_lines:
        key = merchant_key(bank.get("payment_ref"))
        if not key:
            continue
        move_lines = rec_by_move.get(rel_id(bank.get("move_id")), [])
        for ml in move_lines:
            account_name = rel_name(ml.get("account_id"))
            if account_name in {"Bank Suspense Account", "Operating Checking - SEC Laurel"}:
                continue
            if "Bank" in account_name and "Charge" not in account_name:
                continue
            account_id = rel_id(ml.get("account_id"))
            partner_id = rel_id(ml.get("partner_id")) or False
            mapping_counter[key][(account_id, partner_id, account_name, rel_name(ml.get("partner_id")))] += 1
            mapping_example.setdefault(key, bank.get("payment_ref", ""))

    safe_mapping = {}
    for key, counter in mapping_counter.items():
        if not counter:
            continue
        most_common = counter.most_common()
        if len(most_common) == 1 or most_common[0][1] >= most_common[1][1] * 3:
            safe_mapping[key] = most_common[0][0]

    unreconciled = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [("date", ">=", "2026-03-01"), ("date", "<=", "2026-06-30"), ("is_reconciled", "=", False)],
        ["id", "date", "payment_ref", "amount", "move_id"],
        order="date asc",
    )
    unrec_move_ids = [rel_id(line["move_id"]) for line in unreconciled if rel_id(line.get("move_id"))]
    unrec_move_lines = get_move_lines(models, db, uid, api_key, unrec_move_ids)
    unrec_by_move = defaultdict(list)
    for ml in unrec_move_lines:
        unrec_by_move[rel_id(ml["move_id"])].append(ml)

    plan_rows = []
    updates = []
    for bank in unreconciled:
        key = merchant_key(bank.get("payment_ref"))
        if key not in safe_mapping:
            continue
        account_id, partner_id, account_name, partner_name = safe_mapping[key]
        suspense_lines = [
            ml for ml in unrec_by_move.get(rel_id(bank.get("move_id")), [])
            if rel_name(ml.get("account_id")) == "Bank Suspense Account"
        ]
        if len(suspense_lines) != 1:
            continue
        suspense = suspense_lines[0]
        updates.append((suspense["id"], account_id, partner_id))
        plan_rows.append(
            {
                "Bank Statement Line ID": bank["id"],
                "Date": bank.get("date", ""),
                "Payment Ref": bank.get("payment_ref", ""),
                "Amount": bank.get("amount", ""),
                "Merchant Key": key,
                "Suspense Move Line ID": suspense["id"],
                "New Account ID": account_id,
                "New Account": account_name,
                "New Partner ID": partner_id or "",
                "New Partner": partner_name,
                "History Example": mapping_example.get(key, ""),
            }
        )

    fields = [
        "Bank Statement Line ID",
        "Date",
        "Payment Ref",
        "Amount",
        "Merchant Key",
        "Suspense Move Line ID",
        "New Account ID",
        "New Account",
        "New Partner ID",
        "New Partner",
        "History Example",
    ]
    with PLAN.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan_rows)

    if apply:
        for line_id, account_id, partner_id in updates:
            vals = {"account_id": account_id}
            if partner_id:
                vals["partner_id"] = partner_id
            execute(models, db, uid, api_key, "account.move.line", "write", [[line_id], vals])

    print(f"Connected uid: {uid}")
    print(f"Historical merchant mappings learned: {len(safe_mapping)}")
    print(f"Auto-code candidates: {len(plan_rows)}")
    print(f"Applied: {apply}")
    print(f"Plan: {PLAN}")


if __name__ == "__main__":
    main()
