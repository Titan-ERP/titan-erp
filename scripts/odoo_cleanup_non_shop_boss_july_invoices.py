import csv
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SRC = ROOT / "odoo_imports" / "shop_boss" / "odoo_all_july_invoice_shop_boss_coverage_audit_2026_07.csv"
WIP_XREF = ROOT / "odoo_imports" / "shop_boss" / "odoo_july_non_shop_boss_vs_wip_cross_reference.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_july_non_shop_boss_invoice_cleanup_results.csv"


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


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(rows):
    fields = [
        "Status", "Action", "Odoo Invoice ID", "Odoo Invoice", "Odoo State", "Odoo Payment State",
        "Odoo Customer", "Odoo Total", "WIP Cross Reference Status", "Reason",
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

    coverage = [
        row for row in read_csv(SRC)
        if row["Coverage Status"] == "No Shop Boss match in finalized/closed report"
    ]
    wip_by_id = {row["Odoo Invoice ID"]: row for row in read_csv(WIP_XREF)}
    ids = [int(row["Odoo Invoice ID"]) for row in coverage]
    live = execute(
        models, db, uid, api_key, "account.move", "read", [ids],
        {"fields": ["id", "name", "state", "payment_state", "partner_id", "amount_total"]},
    )
    live_by_id = {str(row["id"]): row for row in live}
    results = []

    for source in coverage:
        invoice_id = source["Odoo Invoice ID"]
        inv = live_by_id[invoice_id]
        partner = inv["partner_id"][1] if isinstance(inv.get("partner_id"), list) else ""
        wip_status = wip_by_id.get(invoice_id, {}).get("WIP Cross Reference Status", "")
        result = {
            "Odoo Invoice ID": invoice_id,
            "Odoo Invoice": inv["name"],
            "Odoo State": inv["state"],
            "Odoo Payment State": inv["payment_state"],
            "Odoo Customer": partner,
            "Odoo Total": inv["amount_total"],
            "WIP Cross Reference Status": wip_status,
        }
        if inv["state"] == "draft":
            result["Action"] = "delete_draft"
            result["Reason"] = "Draft Odoo-only invoice; no Shop Boss finalized/closed or strong WIP match."
            if apply:
                try:
                    execute(models, db, uid, api_key, "account.move", "unlink", [[int(invoice_id)]])
                    result["Status"] = "Deleted"
                except Exception as exc:
                    result["Status"] = "Review - delete failed"
                    result["Reason"] = f"{result['Reason']} Error: {exc}"
            else:
                result["Status"] = "Ready"
        elif inv["state"] == "posted" and inv["payment_state"] == "not_paid":
            result["Action"] = "cancel_posted_unpaid"
            result["Reason"] = "Posted unpaid Odoo-only invoice; no Shop Boss finalized/closed or strong WIP match."
            if apply:
                try:
                    execute_void_ok(models, db, uid, api_key, "account.move", "button_cancel", [[int(invoice_id)]])
                    after = execute(models, db, uid, api_key, "account.move", "read", [[int(invoice_id)]], {"fields": ["state"]})[0]
                    result["Status"] = "Cancelled" if after["state"] == "cancel" else f"Review - state {after['state']}"
                except Exception as exc:
                    result["Status"] = "Review - cancel failed"
                    result["Reason"] = f"{result['Reason']} Error: {exc}"
            else:
                result["Status"] = "Ready"
        else:
            result["Action"] = "defer_reversal_payment_review"
            result["Reason"] = "Invoice is paid or in-payment; requires credit note/reversal and payment review."
            result["Status"] = "Deferred"
        results.append(result)

    write_csv(results)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(results)}")
    print(f"Output: {OUT}")
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
