import csv
import os
import xmlrpc.client
from datetime import date
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports" / "operational_details"


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


def read(models, db, uid, api_key, model, domain, field_names, limit=200, order=None, context=None):
    kwargs = {"fields": field_names, "limit": limit}
    if order:
        kwargs["order"] = order
    if context:
        kwargs["context"] = context
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def flatten(rows):
    flattened = []
    for row in rows:
        clean = {}
        for key, value in row.items():
            if isinstance(value, list):
                clean[key] = rel(value) if len(value) == 2 and isinstance(value[0], int) else "; ".join(map(str, value))
            else:
                clean[key] = value
        flattened.append(clean)
    return flattened


def write_csv(name, rows, fields_list):
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORT_DIR / name
    rows = flatten(rows)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields_list, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    return path, len(rows)


def main():
    db, uid, api_key, models = connect()
    today = date.today().isoformat()
    written = []

    overdue_moves = read(
        models,
        db,
        uid,
        api_key,
        "account.move",
        [
            ("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund"]),
            ("state", "=", "posted"),
            ("payment_state", "in", ["not_paid", "partial"]),
            ("invoice_date_due", "<", today),
        ],
        ["name", "move_type", "partner_id", "invoice_date", "invoice_date_due", "amount_total", "amount_residual", "payment_state"],
        order="invoice_date_due asc",
    )
    written.append(write_csv("accounting_overdue_documents.csv", overdue_moves, ["name", "move_type", "partner_id", "invoice_date", "invoice_date_due", "amount_total", "amount_residual", "payment_state"]))

    missing_tax_products = read(
        models,
        db,
        uid,
        api_key,
        "product.template",
        [("sale_ok", "=", True), ("taxes_id", "=", False)],
        ["default_code", "name", "categ_id", "list_price", "sale_ok", "purchase_ok"],
        context={"active_test": False},
    )
    written.append(write_csv("accounting_sale_products_missing_tax.csv", missing_tax_products, ["default_code", "name", "categ_id", "list_price", "sale_ok", "purchase_ok"]))

    sale_to_invoice = read(
        models,
        db,
        uid,
        api_key,
        "sale.order",
        [("state", "in", ["sale", "done"]), ("invoice_status", "=", "to invoice")],
        ["name", "partner_id", "date_order", "state", "invoice_status", "amount_total", "user_id"],
        order="date_order asc",
    )
    written.append(write_csv("sales_orders_to_invoice.csv", sale_to_invoice, ["name", "partner_id", "date_order", "state", "invoice_status", "amount_total", "user_id"]))

    purch_not_sale = read(
        models,
        db,
        uid,
        api_key,
        "product.template",
        [("purchase_ok", "=", True), ("sale_ok", "=", False)],
        ["default_code", "name", "categ_id", "standard_price", "list_price", "active"],
        context={"active_test": False},
    )
    written.append(write_csv("sales_purchase_products_not_saleable.csv", purch_not_sale, ["default_code", "name", "categ_id", "standard_price", "list_price", "active"]))

    sale_fields = fields(models, db, uid, api_key, "sale.order")
    if "is_rental_order" in sale_fields:
        rental_field_list = ["name", "partner_id", "date_order", "state", "amount_total", "invoice_status"]
        for optional in ["rental_status", "rental_start_date", "rental_return_date", "pickup_date", "return_date"]:
            if optional in sale_fields:
                rental_field_list.append(optional)
        rental_orders = read(
            models,
            db,
            uid,
            api_key,
            "sale.order",
            [("is_rental_order", "=", True)],
            rental_field_list,
            order="date_order asc",
        )
        written.append(write_csv("rental_orders.csv", rental_orders, rental_field_list))

    task_fields = fields(models, db, uid, api_key, "project.task")
    if task_fields:
        fsm_domain = [("is_fsm", "=", True)] if "is_fsm" in task_fields else []
        task_field_list = ["name", "partner_id", "stage_id", "date_deadline"]
        for optional in ["user_ids", "project_id", "sale_order_id", "fsm_done", "planned_date_begin", "planned_date_end"]:
            if optional in task_fields:
                task_field_list.append(optional)

        if "user_ids" in task_fields:
            unassigned = read(
                models,
                db,
                uid,
                api_key,
                "project.task",
                fsm_domain + [("user_ids", "=", False)],
                task_field_list,
                order="date_deadline asc",
            )
            written.append(write_csv("field_service_unassigned_tasks.csv", unassigned, task_field_list))

        if "date_deadline" in task_fields:
            overdue_tasks = read(
                models,
                db,
                uid,
                api_key,
                "project.task",
                fsm_domain + [("date_deadline", "<", today)],
                task_field_list,
                order="date_deadline asc",
            )
            written.append(write_csv("field_service_past_due_tasks.csv", overdue_tasks, task_field_list))

    category_fields = fields(models, db, uid, api_key, "product.category")
    category_field_list = ["complete_name"]
    for optional in ["property_account_income_categ_id", "property_account_expense_categ_id", "property_stock_valuation_account_id"]:
        if optional in category_fields:
            category_field_list.append(optional)
    if len(category_field_list) > 1:
        categories = read(
            models,
            db,
            uid,
            api_key,
            "product.category",
            [("complete_name", "ilike", "Parts")],
            category_field_list,
            limit=500,
            order="complete_name asc",
        )
        written.append(write_csv("accounting_parts_category_accounts.csv", categories, category_field_list))

    print(f"Connected uid: {uid}")
    for path, rows in written:
        print(f"{path.name}: {rows}")


if __name__ == "__main__":
    main()
