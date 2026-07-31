import csv
import os
import xmlrpc.client
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports"
SUMMARY_PATH = REPORT_DIR / "odoo_operational_inefficiency_audit.md"
FINDINGS_PATH = REPORT_DIR / "odoo_operational_inefficiency_findings.csv"


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
    return bool(
        execute(
            models,
            db,
            uid,
            api_key,
            "ir.model",
            "search_count",
            [[("model", "=", model)]],
        )
    )


def fields(models, db, uid, api_key, model):
    if not model_exists(models, db, uid, api_key, model):
        return {}
    return execute(models, db, uid, api_key, model, "fields_get", [], {"attributes": ["string", "type"]})


def has(field_map, *names):
    return all(name in field_map for name in names)


def count(models, db, uid, api_key, model, domain=None, context=None):
    kwargs = {}
    if context:
        kwargs["context"] = context
    return execute(models, db, uid, api_key, model, "search_count", [domain or []], kwargs)


def read(models, db, uid, api_key, model, domain=None, field_names=None, limit=100):
    return execute(
        models,
        db,
        uid,
        api_key,
        model,
        "search_read",
        [domain or []],
        {"fields": field_names or ["id", "display_name"], "limit": limit},
    )


def group_count(models, db, uid, api_key, model, domain, fields_list, groupby):
    try:
        return execute(
            models,
            db,
            uid,
            api_key,
            model,
            "read_group",
            [domain, fields_list, groupby],
            {"lazy": False},
        )
    except xmlrpc.client.Fault:
        return []


def write_csv(path, rows, fields_list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields_list, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def relation_name(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def add_finding(findings, module, severity, issue, evidence, recommendation):
    findings.append(
        {
            "Module": module,
            "Severity": severity,
            "Issue": issue,
            "Evidence": evidence,
            "Recommendation": recommendation,
        }
    )


def audit_accounting(models, db, uid, api_key, findings, lines):
    module = "Accounting"
    if not model_exists(models, db, uid, api_key, "account.move"):
        add_finding(findings, module, "High", "Accounting app data unavailable", "account.move model not found", "Confirm Accounting is installed and accessible.")
        return

    today = date.today().isoformat()
    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
    invoice_domain = [("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"])]
    total_moves = count(models, db, uid, api_key, "account.move", invoice_domain)
    draft_invoices = count(models, db, uid, api_key, "account.move", invoice_domain + [("state", "=", "draft")])
    draft_old = count(models, db, uid, api_key, "account.move", invoice_domain + [("state", "=", "draft"), ("invoice_date", "<", thirty_days_ago)])
    overdue = count(
        models,
        db,
        uid,
        api_key,
        "account.move",
        invoice_domain
        + [
            ("state", "=", "posted"),
            ("payment_state", "in", ["not_paid", "partial"]),
            ("invoice_date_due", "<", today),
        ],
    )
    unpaid = count(
        models,
        db,
        uid,
        api_key,
        "account.move",
        invoice_domain + [("state", "=", "posted"), ("payment_state", "in", ["not_paid", "partial"])],
    )
    lines.append(f"- Accounting moves/invoices reviewed: `{total_moves}`")
    lines.append(f"- Draft invoices/bills: `{draft_invoices}`; older than 30 days: `{draft_old}`")
    lines.append(f"- Posted unpaid/partial invoices or bills: `{unpaid}`; overdue: `{overdue}`")

    if draft_old:
        add_finding(findings, module, "Medium", "Old draft invoices or vendor bills are accumulating", f"{draft_old} draft accounting documents are older than 30 days.", "Review old drafts weekly: post valid documents, cancel mistakes, and keep draft state for true work-in-progress only.")
    if overdue:
        add_finding(findings, module, "High", "Overdue unpaid accounting documents need collection/payment workflow attention", f"{overdue} posted documents are overdue and unpaid or partially paid.", "Use aging reports and scheduled follow-up activities; separate customer collections from vendor bill payment review.")

    product_fields = fields(models, db, uid, api_key, "product.template")
    if has(product_fields, "property_account_income_id", "property_account_expense_id"):
        no_income = count(models, db, uid, api_key, "product.template", [("sale_ok", "=", True), ("property_account_income_id", "=", False)], {"active_test": False})
        no_expense = count(models, db, uid, api_key, "product.template", [("purchase_ok", "=", True), ("property_account_expense_id", "=", False)], {"active_test": False})
        lines.append(f"- Sale products without product-level income account: `{no_income}`")
        lines.append(f"- Purchase products without product-level expense account: `{no_expense}`")
        if no_income or no_expense:
            add_finding(findings, module, "Low", "Most products rely on category accounting defaults", f"{no_income} sale products lack product-level income accounts; {no_expense} purchase products lack product-level expense accounts.", "This is fine if categories are configured correctly. Verify each major `Parts / ...` category has the right income and expense accounts.")

    if has(product_fields, "taxes_id", "supplier_taxes_id"):
        no_sales_tax = count(models, db, uid, api_key, "product.template", [("sale_ok", "=", True), ("taxes_id", "=", False)], {"active_test": False})
        no_purchase_tax = count(models, db, uid, api_key, "product.template", [("purchase_ok", "=", True), ("supplier_taxes_id", "=", False)], {"active_test": False})
        lines.append(f"- Sale products without sales tax: `{no_sales_tax}`")
        lines.append(f"- Purchase products without purchase tax: `{no_purchase_tax}`")
        if no_sales_tax:
            add_finding(findings, module, "High", "Products missing sales taxes can create under-collected tax", f"{no_sales_tax} saleable products have no sales tax set.", "Set taxes at category/product level or confirm fiscal-position logic intentionally applies taxes elsewhere.")


def audit_sales(models, db, uid, api_key, findings, lines):
    module = "Sales"
    if not model_exists(models, db, uid, api_key, "sale.order"):
        add_finding(findings, module, "High", "Sales app data unavailable", "sale.order model not found", "Confirm Sales is installed and accessible.")
        return

    sale_fields = fields(models, db, uid, api_key, "sale.order")
    thirty_days_ago = (date.today() - timedelta(days=30)).isoformat()
    total = count(models, db, uid, api_key, "sale.order", [])
    draft = count(models, db, uid, api_key, "sale.order", [("state", "=", "draft")])
    sent = count(models, db, uid, api_key, "sale.order", [("state", "=", "sent")])
    stale_quotes = count(models, db, uid, api_key, "sale.order", [("state", "in", ["draft", "sent"]), ("date_order", "<", thirty_days_ago)])
    sale_orders = count(models, db, uid, api_key, "sale.order", [("state", "in", ["sale", "done"])])
    lines.append(f"- Sales orders/quotations reviewed: `{total}`")
    lines.append(f"- Quotations: `{draft}` draft, `{sent}` sent; stale over 30 days: `{stale_quotes}`")
    lines.append(f"- Confirmed/done sales orders: `{sale_orders}`")

    if stale_quotes:
        add_finding(findings, module, "Medium", "Old quotations are likely cluttering the pipeline", f"{stale_quotes} draft/sent quotations are older than 30 days.", "Create a quotation cleanup cadence: won/lost/cancel stale quotes and require next activity on open quotes.")

    if "invoice_status" in sale_fields:
        to_invoice = count(models, db, uid, api_key, "sale.order", [("state", "in", ["sale", "done"]), ("invoice_status", "=", "to invoice")])
        upselling = count(models, db, uid, api_key, "sale.order", [("state", "in", ["sale", "done"]), ("invoice_status", "=", "upselling")])
        lines.append(f"- Confirmed sales needing invoice action: `{to_invoice}`; upselling invoice status: `{upselling}`")
        if to_invoice:
            add_finding(findings, module, "High", "Confirmed sales orders are waiting to be invoiced", f"{to_invoice} confirmed sales orders have invoice_status = to invoice.", "Review invoice policy and delivery status; invoice eligible orders before month-end close.")

    if "user_id" in sale_fields:
        no_salesperson = count(models, db, uid, api_key, "sale.order", [("user_id", "=", False), ("state", "not in", ["cancel"])])
        lines.append(f"- Open non-cancelled sales orders without salesperson: `{no_salesperson}`")
        if no_salesperson:
            add_finding(findings, module, "Low", "Sales ownership is incomplete", f"{no_salesperson} non-cancelled sales orders have no salesperson.", "Assign salesperson/team defaults so quotes and follow-ups do not become orphaned.")

    product_fields = fields(models, db, uid, api_key, "product.template")
    if "sale_ok" in product_fields:
        purch_not_sale = count(models, db, uid, api_key, "product.template", [("purchase_ok", "=", True), ("sale_ok", "=", False)], {"active_test": False})
        lines.append(f"- Purchasable products not saleable: `{purch_not_sale}`")
        if purch_not_sale:
            add_finding(findings, module, "Medium", "Some parts may not be sellable from orders", f"{purch_not_sale} purchasable products have sale_ok disabled.", "Confirm these are intentionally purchase-only; otherwise enable Sales on parts that counter staff should sell.")


def audit_rental(models, db, uid, api_key, findings, lines):
    module = "Rental"
    sale_fields = fields(models, db, uid, api_key, "sale.order")
    product_fields = fields(models, db, uid, api_key, "product.template")
    if not sale_fields:
        return

    rental_order_flags = [name for name in ["is_rental_order", "rental_status"] if name in sale_fields]
    rental_product_flags = [name for name in ["rent_ok", "rental_ok"] if name in product_fields]
    lines.append(f"- Rental order fields detected: `{', '.join(rental_order_flags) or 'none'}`")
    lines.append(f"- Rental product fields detected: `{', '.join(rental_product_flags) or 'none'}`")

    if not rental_order_flags and not rental_product_flags:
        add_finding(findings, module, "Medium", "Rental configuration is installed visually but not obvious in API fields", "No standard rental flags were detected on sale.order/product.template.", "Confirm Rental app configuration and whether custom modules renamed rental fields.")
        return

    if "is_rental_order" in sale_fields:
        rental_orders = count(models, db, uid, api_key, "sale.order", [("is_rental_order", "=", True)])
        active_rentals = count(models, db, uid, api_key, "sale.order", [("is_rental_order", "=", True), ("state", "in", ["sale", "done"])])
        lines.append(f"- Rental orders: `{rental_orders}`; confirmed/done: `{active_rentals}`")
        if rental_orders == 0:
            add_finding(findings, module, "Low", "Rental app appears underused", "No sale.order records are flagged as rental orders.", "If rental operations are live, validate that rental products and rental quotation flow are being used instead of regular sales orders.")

    if "rental_status" in sale_fields:
        groups = group_count(models, db, uid, api_key, "sale.order", [], ["rental_status"], ["rental_status"])
        status_counts = ", ".join(f"{row.get('rental_status') or 'blank'}={row.get('rental_status_count', row.get('__count'))}" for row in groups)
        lines.append(f"- Rental status counts: `{status_counts or 'none'}`")

    for product_flag in rental_product_flags[:1]:
        rental_products = count(models, db, uid, api_key, "product.template", [(product_flag, "=", True)], {"active_test": False})
        lines.append(f"- Rental-enabled products via `{product_flag}`: `{rental_products}`")
        if rental_products == 0:
            add_finding(findings, module, "High", "No rental-enabled products found", f"0 products have {product_flag}=True.", "Rental cannot run cleanly until rentable equipment/products are configured with rental pricing and availability rules.")


def audit_field_service(models, db, uid, api_key, findings, lines):
    module = "Field Service"
    task_fields = fields(models, db, uid, api_key, "project.task")
    if not task_fields:
        add_finding(findings, module, "High", "Field Service task data unavailable", "project.task model not found", "Confirm Project/Field Service modules are installed and accessible.")
        return

    fsm_domain = []
    if "is_fsm" in task_fields:
        fsm_domain = [("is_fsm", "=", True)]

    total_tasks = count(models, db, uid, api_key, "project.task", fsm_domain)
    lines.append(f"- Field service task domain: `{fsm_domain or 'all project tasks; is_fsm field not detected'}`")
    lines.append(f"- Field service/project tasks reviewed: `{total_tasks}`")

    if "stage_id" in task_fields:
        groups = group_count(models, db, uid, api_key, "project.task", fsm_domain, ["stage_id"], ["stage_id"])
        stage_counts = ", ".join(f"{relation_name(row.get('stage_id')) or 'blank'}={row.get('stage_id_count', row.get('__count'))}" for row in groups[:8])
        lines.append(f"- Top task stage counts: `{stage_counts or 'none'}`")

    if "user_ids" in task_fields:
        unassigned = count(models, db, uid, api_key, "project.task", fsm_domain + [("user_ids", "=", False)])
        lines.append(f"- Tasks without assignee: `{unassigned}`")
        if unassigned:
            add_finding(findings, module, "High", "Field service tasks without assignees can stall dispatch", f"{unassigned} tasks have no assigned user.", "Require assignment before confirming/scheduling field work.")

    if "partner_id" in task_fields:
        no_customer = count(models, db, uid, api_key, "project.task", fsm_domain + [("partner_id", "=", False)])
        lines.append(f"- Tasks without customer: `{no_customer}`")
        if no_customer:
            add_finding(findings, module, "Medium", "Field service tasks without customers reduce dispatch and billing quality", f"{no_customer} tasks have no customer.", "Require customer on service requests and use equipment/customer defaults where possible.")

    deadline_field = "date_deadline" if "date_deadline" in task_fields else None
    if deadline_field:
        overdue = count(models, db, uid, api_key, "project.task", fsm_domain + [(deadline_field, "<", date.today().isoformat())])
        lines.append(f"- Tasks past deadline: `{overdue}`")
        if overdue:
            add_finding(findings, module, "Medium", "Past-due service tasks need dispatch review", f"{overdue} tasks have deadlines before today.", "Use scheduled views and daily dispatch review to close or reschedule past-due work.")

    if model_exists(models, db, uid, api_key, "account.analytic.line"):
        aal_fields = fields(models, db, uid, api_key, "account.analytic.line")
        if "task_id" in aal_fields:
            timesheet_lines = count(models, db, uid, api_key, "account.analytic.line", [("task_id", "!=", False)])
            lines.append(f"- Timesheet lines linked to tasks: `{timesheet_lines}`")


def main():
    db, uid, api_key, models = connect()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    findings = []
    lines = [
        "# Odoo Operational Inefficiency Audit",
        "",
        f"Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"Database: `{db}`",
        "",
        "## Summary Metrics",
    ]

    audit_accounting(models, db, uid, api_key, findings, lines)
    audit_sales(models, db, uid, api_key, findings, lines)
    audit_rental(models, db, uid, api_key, findings, lines)
    audit_field_service(models, db, uid, api_key, findings, lines)

    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    findings.sort(key=lambda row: (severity_order.get(row["Severity"], 9), row["Module"], row["Issue"]))

    lines.extend(["", "## Findings"])
    if not findings:
        lines.append("- No major inefficiencies detected in the read-only audit.")
    else:
        for idx, finding in enumerate(findings, start=1):
            lines.append(f"{idx}. **[{finding['Severity']}] {finding['Module']} - {finding['Issue']}**")
            lines.append(f"   Evidence: {finding['Evidence']}")
            lines.append(f"   Recommendation: {finding['Recommendation']}")

    write_csv(FINDINGS_PATH, findings, ["Module", "Severity", "Issue", "Evidence", "Recommendation"])
    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    severity_counts = Counter(row["Severity"] for row in findings)
    print(f"Connected uid: {uid}")
    print(f"Findings total: {len(findings)}")
    print(f"High: {severity_counts.get('High', 0)}")
    print(f"Medium: {severity_counts.get('Medium', 0)}")
    print(f"Low: {severity_counts.get('Low', 0)}")
    print(f"Summary: {SUMMARY_PATH}")
    print(f"Findings CSV: {FINDINGS_PATH}")


if __name__ == "__main__":
    main()
