import csv
import os
import xmlrpc.client
from collections import Counter
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"


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


def model_exists(models, db, uid, api_key, model):
    return bool(execute(models, db, uid, api_key, "ir.model", "search_count", [[("model", "=", model)]]))


def fields(models, db, uid, api_key, model):
    if not model_exists(models, db, uid, api_key, model):
        return {}
    return execute(models, db, uid, api_key, model, "fields_get", [], {"attributes": ["string", "type"]})


def read(models, db, uid, api_key, model, domain, field_names, limit=5000, order=None, context=None):
    kwargs = {"fields": field_names, "limit": limit}
    if order:
        kwargs["order"] = order
    if context:
        kwargs["context"] = context
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return value


def flatten(rows):
    out = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            clean[key] = rel(value)
        out.append(clean)
    return out


def write_csv(path, rows, field_names):
    OUT.mkdir(parents=True, exist_ok=True)
    rows = flatten(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=field_names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    db, uid, api_key, models = connect()
    OUT.mkdir(parents=True, exist_ok=True)

    bsl_fields = fields(models, db, uid, api_key, "account.bank.statement.line")
    print(f"Connected uid: {uid}")
    print(f"account.bank.statement.line fields detected: {len(bsl_fields)}")

    wanted = [
        "id",
        "display_name",
        "date",
        "payment_ref",
        "partner_id",
        "amount",
        "currency_id",
        "journal_id",
        "is_reconciled",
        "move_id",
        "statement_id",
    ]
    bsl_read_fields = [name for name in wanted if name in bsl_fields]
    domain = []
    if "date" in bsl_fields:
        domain += [("date", ">=", "2026-03-01"), ("date", "<=", "2026-06-30")]
    lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        domain,
        bsl_read_fields,
        order="date asc" if "date" in bsl_fields else None,
        context={"active_test": False},
    )
    write_csv(OUT / "odoo_bank_statement_lines_2026_03_to_06.csv", lines, bsl_read_fields)

    reconciled_counts = Counter(str(row.get("is_reconciled")) for row in lines)
    by_journal = Counter(rel(row.get("journal_id")) or "blank" for row in lines)
    unreconciled = [row for row in lines if row.get("is_reconciled") is False]
    write_csv(OUT / "odoo_unreconciled_bank_statement_lines_2026_03_to_06.csv", unreconciled, bsl_read_fields)

    aml_fields = fields(models, db, uid, api_key, "account.move.line")
    aml_wanted = [
        "id",
        "date",
        "name",
        "ref",
        "partner_id",
        "account_id",
        "journal_id",
        "move_id",
        "debit",
        "credit",
        "balance",
        "amount_residual",
        "reconciled",
        "matching_number",
    ]
    aml_read_fields = [name for name in aml_wanted if name in aml_fields]
    aml_domain = [("date", ">=", "2026-03-01"), ("date", "<=", "2026-06-30")]
    if "reconciled" in aml_fields:
        aml_domain.append(("reconciled", "=", False))
    open_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        aml_domain,
        aml_read_fields,
        limit=10000,
        order="date asc",
        context={"active_test": False},
    )
    write_csv(OUT / "odoo_open_account_move_lines_2026_03_to_06.csv", open_lines, aml_read_fields)

    print(f"Bank statement lines 2026-03..06: {len(lines)}")
    print(f"Bank statement reconciled states: {dict(reconciled_counts)}")
    print(f"Unreconciled bank statement lines: {len(unreconciled)}")
    print(f"Bank journals: {dict(by_journal)}")
    print(f"Open account move lines 2026-03..06: {len(open_lines)}")
    print(f"Output folder: {OUT}")


if __name__ == "__main__":
    main()
