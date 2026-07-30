import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SRC = ROOT / "odoo_imports" / "shop_boss" / "odoo_all_july_invoice_shop_boss_coverage_audit_2026_07.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_july_non_shop_boss_invoice_reversal_results.csv"


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


def write_csv(rows):
    fields = [
        "Status", "Odoo Invoice ID", "Odoo Invoice", "Odoo Payment State", "Odoo Total",
        "Credit Note ID", "Credit Note", "Credit Note State", "Reason",
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

    source_rows = [
        row for row in read_csv(SRC)
        if row["Coverage Status"] == "No Shop Boss match in finalized/closed report"
    ]
    ids = [int(row["Odoo Invoice ID"]) for row in source_rows]
    live = execute(
        models, db, uid, api_key, "account.move", "read", [ids],
        {"fields": ["id", "name", "state", "payment_state", "amount_total", "invoice_date", "journal_id", "company_id", "reversal_move_ids"]},
    )
    live_by_id = {str(row["id"]): row for row in live}
    results = []

    for source in source_rows:
        inv = live_by_id.get(source["Odoo Invoice ID"])
        if not inv or inv["state"] in {"cancel", "draft"}:
            continue
        if inv["payment_state"] not in {"paid", "in_payment"}:
            continue
        existing_reversals = inv.get("reversal_move_ids") or []
        base = {
            "Odoo Invoice ID": inv["id"],
            "Odoo Invoice": inv["name"],
            "Odoo Payment State": inv["payment_state"],
            "Odoo Total": inv["amount_total"],
        }
        if existing_reversals:
            reversal = execute(
                models, db, uid, api_key, "account.move", "read", [existing_reversals],
                {"fields": ["id", "name", "state"]},
            )[0]
            results.append({
                **base,
                "Status": "Skipped",
                "Credit Note ID": reversal["id"],
                "Credit Note": reversal["name"],
                "Credit Note State": reversal["state"],
                "Reason": "Original invoice already has a reversal/credit note.",
            })
            continue
        if not apply:
            results.append({
                **base,
                "Status": "Ready",
                "Reason": "Would create and post credit note for Odoo-only paid/in-payment invoice.",
            })
            continue

        vals = {
            "move_ids": [(6, 0, [inv["id"]])],
            "journal_id": inv["journal_id"][0],
            "company_id": inv["company_id"][0],
            "date": inv["invoice_date"],
            "reason": "Non-Shop-Boss July cleanup reversal",
        }
        context = {"active_model": "account.move", "active_ids": [inv["id"]], "active_id": inv["id"]}
        wizard_id = execute(models, db, uid, api_key, "account.move.reversal", "create", [vals], {"context": context})
        action = execute(models, db, uid, api_key, "account.move.reversal", "reverse_moves", [[wizard_id]], {"context": context})
        reversal_id = action.get("res_id") if isinstance(action, dict) else None
        if not reversal_id:
            reversals = execute(models, db, uid, api_key, "account.move", "search_read", [[("reversed_entry_id", "=", inv["id"])]], {"fields": ["id"], "limit": 1})
            reversal_id = reversals[0]["id"] if reversals else None
        if not reversal_id:
            results.append({**base, "Status": "Review", "Reason": "Reversal wizard did not return or create a credit note."})
            continue
        execute(models, db, uid, api_key, "account.move", "action_post", [[reversal_id]])
        reversal = execute(
            models, db, uid, api_key, "account.move", "read", [[reversal_id]],
            {"fields": ["id", "name", "state"]},
        )[0]
        results.append({
            **base,
            "Status": "Reversed",
            "Credit Note ID": reversal["id"],
            "Credit Note": reversal["name"],
            "Credit Note State": reversal["state"],
            "Reason": "Created and posted credit note for Odoo-only paid/in-payment invoice.",
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
