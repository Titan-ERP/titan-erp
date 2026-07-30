import csv
import os
import re
import xmlrpc.client
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SRC = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_missing_part_sales_invoice_detail_2026_07.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "odoo_created_shop_boss_draft_invoices_2026_07.csv"

COMPANY_NAME = "Southern Equipment Company (Laurel)"
SALES_JOURNAL_NAME = "Sales"
PARTS_INCOME_ACCOUNT_NAME = "Parts Revenue"
SALES_TAX_NAME = "MS Sales Tax - 7%"

PARTNER_FALLBACKS = {
    "COVINGTON COUNTY": {
        "name": "COVINGTON COUNTY",
        "email": "accountspayable@covingtoncountyms.gov",
    },
}


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
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "Status",
        "Shop Boss PS",
        "Customer",
        "Odoo Partner ID",
        "Odoo Partner",
        "Odoo Invoice ID",
        "Odoo Invoice",
        "Invoice Date",
        "Parts",
        "Tax",
        "Total",
        "Reason",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def norm(value):
    text = re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper())
    return re.sub(r"\s+", " ", text).strip()


def money(value):
    return Decimal(str(value or "0").replace("$", "").replace(",", "").strip() or "0").quantize(Decimal("0.01"))


def mmddyyyy_to_iso(value):
    month, day, year = str(value).split("/")
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def single(rows, label):
    if len(rows) != 1:
        raise SystemExit(f"Expected exactly one {label}; found {len(rows)}")
    return rows[0]


def find_partner(models, db, uid, api_key, customer, apply):
    rows = execute(models, db, uid, api_key, "res.partner", "search_read", [[("name", "ilike", customer)]], {"fields": ["id", "name"], "limit": 20})
    exact = [row for row in rows if norm(row["name"]) == norm(customer)]
    if len(exact) == 1:
        return exact[0], False
    contains = [row for row in rows if norm(customer) in norm(row["name"]) or norm(row["name"]) in norm(customer)]
    if len(contains) == 1:
        return contains[0], False
    fallback = PARTNER_FALLBACKS.get(norm(customer))
    if fallback and apply:
        partner_id = execute(models, db, uid, api_key, "res.partner", "create", [fallback])
        return {"id": partner_id, "name": fallback["name"]}, True
    if fallback:
        return {"id": "", "name": fallback["name"]}, False
    return None, False


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

    company = single(execute(models, db, uid, api_key, "res.company", "search_read", [[("name", "=", COMPANY_NAME)]], {"fields": ["id", "name"], "limit": 2}), COMPANY_NAME)
    journal = single(execute(models, db, uid, api_key, "account.journal", "search_read", [[("name", "=", SALES_JOURNAL_NAME), ("company_id", "=", company["id"])]], {"fields": ["id", "name"], "limit": 2}), SALES_JOURNAL_NAME)
    income = single(
        execute(
            models,
            db,
            uid,
            api_key,
            "account.account",
            "search_read",
            [[("name", "=", PARTS_INCOME_ACCOUNT_NAME), ("company_ids", "in", [company["id"]])]],
            {"fields": ["id", "code", "name"], "limit": 2},
        ),
        PARTS_INCOME_ACCOUNT_NAME,
    )
    tax = single(execute(models, db, uid, api_key, "account.tax", "search_read", [[("name", "=", SALES_TAX_NAME), ("company_id", "=", company["id"])]], {"fields": ["id", "name"], "limit": 2}), SALES_TAX_NAME)

    rows = []
    for source in read_csv(SRC):
        ref = f"Shop Boss PS {source['ps_number']}"
        existing = execute(
            models,
            db,
            uid,
            api_key,
            "account.move",
            "search_read",
            [[("move_type", "=", "out_invoice"), ("ref", "=", ref), ("state", "!=", "cancel")]],
            {"fields": ["id", "name", "state"], "limit": 5},
        )
        if existing:
            rows.append(
                {
                    "Status": "Skipped",
                    "Shop Boss PS": source["ps_number"],
                    "Customer": source["customer"],
                    "Odoo Invoice ID": existing[0]["id"],
                    "Odoo Invoice": existing[0]["name"],
                    "Invoice Date": mmddyyyy_to_iso(source["closed_date"]),
                    "Parts": source["parts"],
                    "Tax": source["tax"],
                    "Total": source["total_sale"],
                    "Reason": "Existing non-cancelled Odoo invoice with same Shop Boss ref",
                }
            )
            continue

        partner, created_partner = find_partner(models, db, uid, api_key, source["customer"], apply)
        if not partner:
            rows.append(
                {
                    "Status": "Review",
                    "Shop Boss PS": source["ps_number"],
                    "Customer": source["customer"],
                    "Invoice Date": mmddyyyy_to_iso(source["closed_date"]),
                    "Parts": source["parts"],
                    "Tax": source["tax"],
                    "Total": source["total_sale"],
                    "Reason": "No unique Odoo partner match and no configured fallback",
                }
            )
            continue

        line_values = {
            "name": ref,
            "quantity": 1.0,
            "price_unit": float(money(source["parts"]) + money(source["fees"])),
            "account_id": income["id"],
        }
        if money(source["tax"]):
            line_values["tax_ids"] = [(6, 0, [tax["id"]])]
        else:
            line_values["tax_ids"] = [(6, 0, [])]

        invoice_values = {
            "move_type": "out_invoice",
            "partner_id": partner["id"],
            "company_id": company["id"],
            "journal_id": journal["id"],
            "invoice_date": mmddyyyy_to_iso(source["closed_date"]),
            "invoice_date_due": mmddyyyy_to_iso(source["closed_date"]),
            "invoice_origin": ref,
            "ref": ref,
            "invoice_line_ids": [(0, 0, line_values)],
        }
        if apply:
            if not partner["id"]:
                partner, created_partner = find_partner(models, db, uid, api_key, source["customer"], True)
                invoice_values["partner_id"] = partner["id"]
            invoice_id = execute(models, db, uid, api_key, "account.move", "create", [invoice_values])
            invoice = execute(models, db, uid, api_key, "account.move", "read", [[invoice_id]], {"fields": ["id", "name"]})[0]
            status = "Created draft"
        else:
            invoice = {"id": "", "name": ""}
            status = "Ready"

        rows.append(
            {
                "Status": status,
                "Shop Boss PS": source["ps_number"],
                "Customer": source["customer"],
                "Odoo Partner ID": partner["id"],
                "Odoo Partner": partner["name"],
                "Odoo Invoice ID": invoice["id"],
                "Odoo Invoice": invoice["name"],
                "Invoice Date": mmddyyyy_to_iso(source["closed_date"]),
                "Parts": source["parts"],
                "Tax": source["tax"],
                "Total": source["total_sale"],
                "Reason": "Created partner from Shop Boss AP email" if created_partner else "",
            }
        )

    write_csv(rows)
    print(f"Connected uid: {uid}")
    print(f"Applied: {apply}")
    print(f"Rows: {len(rows)}")
    print(f"Output: {OUT}")
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
