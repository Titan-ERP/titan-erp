import csv
import os
import re
import sys
import xmlrpc.client
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT_DIR = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26"
SHOP_BOSS_DIR = ROOT / "odoo_imports" / "shop_boss"
COMPANY_NAME = "Southern Equipment Company (Laurel)"


def load_env(path):
    if not path.exists():
        raise SystemExit(f"Missing {path}")
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    load_env(ENV_PATH)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, key, model, method, *args, **kwargs):
    return models.execute_kw(db, uid, key, model, method, list(args), kwargs or {})


def money(value):
    if value in (None, "", False):
        return 0.0
    cleaned = str(value).replace("$", "").replace(",", "").replace("(", "-").replace(")", "").strip()
    return float(cleaned or 0)


def norm_name(value):
    return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()


def ref_token(kind, number):
    return f"{kind}{str(number).strip()}"


def read_shop_boss_refs():
    wip = {}
    with (SHOP_BOSS_DIR / "shop_boss_wip_snapshot_2026_07_25.csv").open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            kind = str(row.get("WIP Type", "")).strip().upper()
            number = str(row.get("WIP Number", "")).strip()
            if not kind or not number:
                continue
            wip[ref_token(kind, number)] = {
                "source": "wip",
                "kind": kind,
                "number": number,
                "status": str(row.get("WIP Status", "")).strip().upper(),
                "date": str(row.get("WIP Date", "")).strip(),
                "customer": str(row.get("WIP Customer", "")).strip(),
                "vehicle": str(row.get("WIP Vehicle", "")).strip(),
            }

    closed = {}
    with (SHOP_BOSS_DIR / "shop_boss_all_invoice_rows_finalized_closed_2026_07.csv").open(
        newline="", encoding="utf-8-sig"
    ) as handle:
        for row in csv.DictReader(handle):
            kind = str(row.get("Shop Boss Type", "")).strip().upper()
            number = str(row.get("Shop Boss Number", "")).strip()
            if not kind or not number:
                continue
            closed[ref_token(kind, number)] = {
                "source": "closed_invoice",
                "kind": kind,
                "number": number,
                "status": "FINALIZED/CLOSED",
                "date": str(row.get("Shop Boss Date ISO") or row.get("Shop Boss Date") or "").strip(),
                "customer": str(row.get("Shop Boss Customer", "")).strip(),
                "labor": money(row.get("Labor")),
                "parts": money(row.get("Parts")),
                "sublet": money(row.get("Sublet")),
                "fees": money(row.get("Fees")),
                "tax": money(row.get("Tax")),
                "total": money(row.get("Total") or row.get("Total Sale")),
            }
    return wip, closed


def extract_shop_boss_ref(order):
    text = " ".join(str(order.get(field) or "") for field in ("client_order_ref", "origin", "note", "name"))
    match = re.search(r"\b(RO|PS)\s*#?\s*(\d+)\b", text, flags=re.I)
    if match:
        return ref_token(match.group(1).upper(), match.group(2))
    return ""


def account_by_name(models, db, uid, key, company_id, name, account_type=None):
    domain = [["company_ids", "in", [company_id]], ["name", "=", name]]
    if account_type:
        domain.append(["account_type", "=", account_type])
    ids = execute(models, db, uid, key, "account.account", "search", domain, limit=1)
    if not ids:
        raise SystemExit(f"Could not find account {name!r}")
    return ids[0]


def audit_orders(models, db, uid, key, company_id, wip_refs, closed_refs):
    orders = execute(
        models,
        db,
        uid,
        key,
        "sale.order",
        "search_read",
        [
            ["company_id", "=", company_id],
            ["date_order", ">=", "2026-01-01"],
            ["date_order", "<", "2026-08-01"],
        ],
        fields=[
            "id",
            "name",
            "date_order",
            "partner_id",
            "state",
            "client_order_ref",
            "origin",
            "amount_total",
            "invoice_status",
            "order_line",
        ],
        order="name",
    )
    rows = []
    for order in orders:
        ref = extract_shop_boss_ref(order)
        partner = order["partner_id"][1] if order.get("partner_id") else ""
        evidence = closed_refs.get(ref) or wip_refs.get(ref) or {}
        if ref and ref in closed_refs:
            status = "verified_closed_invoice_source"
            risk = "review"
            if order["state"] == "draft":
                issue = "Odoo quote remains draft even though Shop Boss has finalized/closed invoice evidence."
            elif order["state"] == "sale" and order["invoice_status"] == "to invoice":
                issue = "Odoo order is ready to invoice, but posted invoice may already exist from Shop Boss import."
            else:
                issue = "Shop Boss closed invoice reference present."
        elif ref and ref in wip_refs:
            status = "verified_shop_boss_wip"
            shop_status = evidence.get("status", "")
            if order["state"] == "sale" and order["invoice_status"] == "to invoice" and shop_status not in ("FINAL",):
                risk = "high"
                issue = "Odoo order is confirmed/ready to invoice, but Shop Boss still shows WIP not final."
            elif order["state"] == "draft" and shop_status == "FINAL":
                risk = "medium"
                issue = "Shop Boss WIP is final but Odoo quote is still draft; verify before invoicing because Shop Boss invoice import may already handle revenue."
            else:
                risk = "ok"
                issue = "Odoo order state is consistent with Shop Boss WIP evidence."
        elif not ref and order["state"] == "draft" and not order["order_line"] and abs(float(order["amount_total"])) < 0.005:
            status = "no_shop_boss_evidence"
            risk = "cleanup_applied"
            issue = "Zero-dollar empty draft quote has no Shop Boss reference."
        else:
            status = "no_direct_shop_boss_reference"
            risk = "review" if order["state"] != "cancel" else "low"
            issue = "No direct Shop Boss RO/PS reference found on Odoo sales order."

        rows.append(
            {
                "odoo_order": order["name"],
                "odoo_id": order["id"],
                "date_order": order.get("date_order", ""),
                "partner": partner,
                "state": order["state"],
                "invoice_status": order["invoice_status"],
                "amount_total": f"{float(order['amount_total']):.2f}",
                "shop_boss_ref": ref,
                "shop_boss_source": evidence.get("source", ""),
                "shop_boss_status": evidence.get("status", ""),
                "shop_boss_customer": evidence.get("customer", ""),
                "shop_boss_date": evidence.get("date", ""),
                "match_status": status,
                "risk": risk,
                "issue": issue,
            }
        )
    return rows


def delete_zero_draft_orders(models, db, uid, key, audit_rows):
    results = []
    for row in audit_rows:
        if row["risk"] != "cleanup_applied":
            continue
        order_id = int(row["odoo_id"])
        before = execute(
            models,
            db,
            uid,
            key,
            "sale.order",
            "read",
            [order_id],
            fields=["name", "state", "amount_total", "order_line", "client_order_ref", "origin"],
        )[0]
        if (
            before["state"] == "draft"
            and abs(float(before["amount_total"])) < 0.005
            and not before["order_line"]
            and not before.get("client_order_ref")
            and not before.get("origin")
        ):
            execute(models, db, uid, key, "sale.order", "unlink", [order_id])
            action = "deleted"
        else:
            action = "skipped_live_guard_failed"
        results.append({"odoo_order": row["odoo_order"], "odoo_id": order_id, "action": action})
    return results


def fix_service_product_accounts(models, db, uid, key, service_income_id, service_expense_id):
    service_codes = ["LABOR-SHOP", "LABOR-FIELD", "F350"]
    domain = [
        "|",
        ["default_code", "in", service_codes],
        ["name", "in", ["SHOP LABOR", "P/up & Delivery"]],
    ]
    products = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search_read",
        domain,
        fields=["id", "name", "default_code", "property_account_income_id", "property_account_expense_id"],
        order="default_code,name",
    )
    results = []
    for product in products:
        current_income = product["property_account_income_id"][0] if product.get("property_account_income_id") else None
        current_expense = product["property_account_expense_id"][0] if product.get("property_account_expense_id") else None
        vals = {}
        if current_income != service_income_id:
            vals["property_account_income_id"] = service_income_id
        if current_expense != service_expense_id:
            vals["property_account_expense_id"] = service_expense_id
        if vals:
            execute(models, db, uid, key, "product.template", "write", [product["id"]], vals)
            action = "updated"
        else:
            action = "already_correct"
        results.append(
            {
                "product_id": product["id"],
                "default_code": product.get("default_code") or "",
                "name": product["name"],
                "previous_income": product["property_account_income_id"][1]
                if product.get("property_account_income_id")
                else "",
                "previous_expense": product["property_account_expense_id"][1]
                if product.get("property_account_expense_id")
                else "",
                "new_income": "Service Revenue",
                "new_expense": "Service Cost of Revenue",
                "action": action,
            }
        )
    return results


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main():
    db, uid, key, models = connect()
    company_ids = execute(models, db, uid, key, "res.company", "search", [["name", "=", COMPANY_NAME]], limit=1)
    if not company_ids:
        raise SystemExit(f"Could not find company {COMPANY_NAME}")
    company_id = company_ids[0]

    wip_refs, closed_refs = read_shop_boss_refs()
    audit_before = audit_orders(models, db, uid, key, company_id, wip_refs, closed_refs)
    delete_results = delete_zero_draft_orders(models, db, uid, key, audit_before)

    service_income_id = account_by_name(models, db, uid, key, company_id, "Service Revenue", "income")
    service_expense_id = account_by_name(models, db, uid, key, company_id, "Service Cost of Revenue", "expense_direct_cost")
    product_results = fix_service_product_accounts(models, db, uid, key, service_income_id, service_expense_id)

    audit_after = audit_orders(models, db, uid, key, company_id, wip_refs, closed_refs)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    write_csv(
        OUT_DIR / "shop_boss_verified_sales_order_anomaly_audit.csv",
        audit_after,
        [
            "odoo_order",
            "odoo_id",
            "date_order",
            "partner",
            "state",
            "invoice_status",
            "amount_total",
            "shop_boss_ref",
            "shop_boss_source",
            "shop_boss_status",
            "shop_boss_customer",
            "shop_boss_date",
            "match_status",
            "risk",
            "issue",
        ],
    )
    write_csv(OUT_DIR / "shop_boss_verified_sales_cleanup_results.csv", delete_results, ["odoo_order", "odoo_id", "action"])
    write_csv(
        OUT_DIR / "service_product_account_fix_results.csv",
        product_results,
        [
            "product_id",
            "default_code",
            "name",
            "previous_income",
            "previous_expense",
            "new_income",
            "new_expense",
            "action",
        ],
    )

    high = [row for row in audit_after if row["risk"] == "high"]
    medium = [row for row in audit_after if row["risk"] == "medium"]
    cleanup_deleted = [row for row in delete_results if row["action"] == "deleted"]
    product_updated = [row for row in product_results if row["action"] == "updated"]
    summary = [
        "# Shop Boss Verified Sales Anomaly Fix",
        "",
        f"Run: {timestamp}",
        f"Company: {COMPANY_NAME}",
        "",
        "## Applied",
        "",
        f"- Deleted zero-dollar empty draft quotes with no Shop Boss evidence: {len(cleanup_deleted)}",
        f"- Updated service-charge products to Service Revenue / Service Cost of Revenue: {len(product_updated)}",
        "",
        "## Remaining Review Items",
        "",
        f"- High-risk sales orders: {len(high)}",
        f"- Medium-risk sales orders: {len(medium)}",
        "",
    ]
    if high:
        summary.append("### High Risk")
        summary.append("")
        for row in high:
            summary.append(
                f"- {row['odoo_order']} {row['partner']} `{row['shop_boss_ref']}` ${float(row['amount_total']):,.2f}: {row['issue']}"
            )
        summary.append("")
    if medium:
        summary.append("### Medium Risk")
        summary.append("")
        for row in medium:
            summary.append(
                f"- {row['odoo_order']} {row['partner']} `{row['shop_boss_ref']}` ${float(row['amount_total']):,.2f}: {row['issue']}"
            )
        summary.append("")
    summary.extend(
        [
            "## Evidence Files",
            "",
            "- `shop_boss_verified_sales_order_anomaly_audit.csv`",
            "- `shop_boss_verified_sales_cleanup_results.csv`",
            "- `service_product_account_fix_results.csv`",
        ]
    )
    (OUT_DIR / "shop_boss_verified_sales_anomaly_fix_summary.md").write_text("\n".join(summary) + "\n", encoding="utf-8")

    print(f"Deleted zero-dollar draft quotes: {len(cleanup_deleted)}")
    print(f"Updated service products: {len(product_updated)}")
    print(f"High-risk review items: {len(high)}")
    print(f"Medium-risk review items: {len(medium)}")
    print(OUT_DIR / "shop_boss_verified_sales_anomaly_fix_summary.md")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
