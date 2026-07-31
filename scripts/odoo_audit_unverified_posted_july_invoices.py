import csv
import os
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SRC = ROOT / "odoo_imports" / "shop_boss" / "odoo_all_july_invoice_shop_boss_coverage_audit_2026_07.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_july_unverified_invoice_final_state.csv"


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


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(rows):
    fields = [
        "Status", "Action", "Odoo Invoice ID", "Odoo Invoice", "Odoo State", "Odoo Payment State", "Odoo Total",
        "Credit Note IDs", "Credit Note Names", "Credit Note States", "Credit Note Total",
        "Reason",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def reversal_ids(value):
    if not value:
        return []
    return [int(part.strip()) for part in str(value).split(",") if part.strip()]


def cancel_moves(models, db, uid, api_key, ids):
    execute_void_ok(models, db, uid, api_key, "account.move", "button_cancel", [ids])


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

    source_rows = [
        row for row in read_csv(SRC)
        if row["Coverage Status"] == "No Shop Boss match in finalized/closed report"
    ]
    ids = [int(row["Odoo Invoice ID"]) for row in source_rows]
    invoices = execute(
        models, db, uid, api_key, "account.move", "read", [ids],
        {"fields": ["id", "name", "state", "payment_state", "amount_total", "reversal_move_ids"]},
    )
    by_id = {str(row["id"]): row for row in invoices}
    all_reversal_ids = sorted({rid for inv in invoices for rid in inv.get("reversal_move_ids", [])})
    reversals = execute(
        models, db, uid, api_key, "account.move", "read", [all_reversal_ids],
        {"fields": ["id", "name", "state", "amount_total", "move_type"]},
    ) if all_reversal_ids else []
    reversal_by_id = {row["id"]: row for row in reversals}
    results = []

    for source in source_rows:
        inv = by_id[source["Odoo Invoice ID"]]
        rev_ids = inv.get("reversal_move_ids") or reversal_ids(source.get("Odoo Reversal IDs"))
        revs = [reversal_by_id[rid] for rid in rev_ids if rid in reversal_by_id]
        credit_total = sum((money(row["amount_total"]) for row in revs), Decimal("0.00"))
        base = {
            "Odoo Invoice ID": inv["id"],
            "Odoo Invoice": inv["name"],
            "Odoo State": inv["state"],
            "Odoo Payment State": inv["payment_state"],
            "Odoo Total": inv["amount_total"],
            "Credit Note IDs": ",".join(str(row["id"]) for row in revs),
            "Credit Note Names": ",".join(row["name"] for row in revs),
            "Credit Note States": ",".join(row["state"] for row in revs),
            "Credit Note Total": credit_total,
        }
        if inv["state"] == "cancel":
            results.append({
                **base,
                "Status": "Already cancelled",
                "Action": "none",
                "Reason": "Odoo-only invoice is not verified by Shop Boss and is already cancelled.",
            })
            continue
        if inv["state"] != "posted":
            results.append({
                **base,
                "Status": "Review",
                "Action": "none",
                "Reason": f"Odoo-only invoice has unexpected state {inv['state']}.",
            })
            continue
        if not revs:
            results.append({
                **base,
                "Status": "Review",
                "Action": "none",
                "Reason": "Posted unverified invoice has no credit note pair; manual review required.",
            })
            continue
        if credit_total != money(inv["amount_total"]):
            results.append({
                **base,
                "Status": "Review",
                "Action": "none",
                "Reason": "Credit note total does not exactly offset the invoice.",
            })
            continue
        if any(row["state"] != "posted" for row in revs):
            results.append({
                **base,
                "Status": "Review",
                "Action": "none",
                "Reason": "Credit note pair is not posted; manual review required.",
            })
            continue
        if not apply:
            results.append({
                **base,
                "Status": "Ready",
                "Action": "cancel_invoice_and_credit_note_pair",
                "Reason": "Would cancel both the unverified posted invoice and its exact posted credit note pair.",
            })
            continue

        pair_ids = [inv["id"], *[row["id"] for row in revs]]
        try:
            cancel_moves(models, db, uid, api_key, pair_ids)
            after = execute(
                models, db, uid, api_key, "account.move", "read", [pair_ids],
                {"fields": ["id", "state"]},
            )
            states = {row["id"]: row["state"] for row in after}
            if all(states.get(move_id) == "cancel" for move_id in pair_ids):
                status = "Cancelled"
                reason = "Cancelled both the unverified invoice and exact credit note pair."
            else:
                status = "Review"
                reason = f"Cancel attempted, but resulting states were {states}."
            results.append({**base, "Status": status, "Action": "cancel_invoice_and_credit_note_pair", "Reason": reason})
        except Exception as exc:
            results.append({
                **base,
                "Status": "Review - cancel failed",
                "Action": "cancel_invoice_and_credit_note_pair",
                "Reason": str(exc),
            })

    write_csv(results)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
