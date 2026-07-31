import argparse
import csv
import os
import xmlrpc.client
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "accounting" / "shop_boss_revenue_bucket_reclass_plan.csv"

COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Miscellaneous Operations"

ACCOUNTS = {
    "parts": "Parts Revenue",
    "service": "Service Revenue",
    "rental": "Rental Revenue",
    "tax": "Sales Tax Payable",
    "fees": "Bank Merchant Fees",
}

# Evidence-backed lines only. Amounts are from Shop Boss source records already
# matched to the listed Laurel bank statement line.
RECLASSES = [
    {
        "bank_line_id": 384,
        "date": "2026-06-09",
        "source": "Shop Boss PS 331",
        "current_parts": "155.11",
        "target_parts": "144.96",
        "service": "0.00",
        "rental": "0.00",
        "tax": "10.15",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 389,
        "date": "2026-06-10",
        "source": "Shop Boss RO 1064; RO 1073",
        "current_parts": "2501.24",
        "target_parts": "593.06",
        "service": "1923.29",
        "rental": "0.00",
        "tax": "0.00",
        "merchant_fee": "15.11",
    },
    {
        "bank_line_id": 390,
        "date": "2026-06-10",
        "source": "Shop Boss RO 1087",
        "current_parts": "2700.00",
        "target_parts": "1763.18",
        "service": "954.15",
        "rental": "0.00",
        "tax": "0.00",
        "merchant_fee": "17.33",
    },
    {
        "bank_line_id": 411,
        "date": "2026-06-12",
        "source": "Shop Boss PS 314",
        "current_parts": "2236.31",
        "target_parts": "2090.01",
        "service": "0.00",
        "rental": "0.00",
        "tax": "146.30",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 457,
        "date": "2026-06-23",
        "source": "Shop Boss RO 1098; PS 355; PS 362",
        "current_parts": "1635.53",
        "target_parts": "597.11",
        "service": "997.24",
        "rental": "0.00",
        "tax": "101.12",
        "merchant_fee": "59.94",
    },
    {
        "bank_line_id": 498,
        "date": "2026-06-30",
        "source": "Shop Boss RO 1092",
        "current_parts": "1760.00",
        "target_parts": "1210.96",
        "service": "551.33",
        "rental": "0.00",
        "tax": "0.00",
        "merchant_fee": "2.29",
    },
    {
        "bank_line_id": 500,
        "date": "2026-06-30",
        "source": "Shop Boss RO 1096",
        "current_parts": "529.79",
        "target_parts": "344.36",
        "service": "185.43",
        "rental": "0.00",
        "tax": "0.00",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 544,
        "date": "2026-07-09",
        "source": "Shop Boss PS 399",
        "current_parts": "14.31",
        "target_parts": "13.37",
        "service": "0.00",
        "rental": "0.00",
        "tax": "0.94",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 545,
        "date": "2026-07-09",
        "source": "Shop Boss RO 1104 payment 1 of 2",
        "current_parts": "854.80",
        "target_parts": "346.77",
        "service": "508.03",
        "rental": "0.00",
        "tax": "0.00",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 527,
        "date": "2026-07-06",
        "source": "Shop Boss PS 392",
        "current_parts": "120.32",
        "target_parts": "112.50",
        "service": "0.00",
        "rental": "0.00",
        "tax": "7.88",
        "merchant_fee": "0.06",
    },
    {
        "bank_line_id": 559,
        "date": "2026-07-13",
        "source": "Shop Boss PS 404",
        "current_parts": "101.44",
        "target_parts": "94.80",
        "service": "0.00",
        "rental": "0.00",
        "tax": "6.64",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 561,
        "date": "2026-07-13",
        "source": "Shop Boss RO 1082",
        "current_parts": "1441.82",
        "target_parts": "832.09",
        "service": "539.96",
        "rental": "0.00",
        "tax": "96.04",
        "merchant_fee": "26.27",
    },
    {
        "bank_line_id": 569,
        "date": "2026-07-14",
        "source": "Shop Boss RO 1108; RO 1109; RO 1110; RO 1111; PS 407; PS 408",
        "current_parts": "1734.25",
        "target_parts": "607.06",
        "service": "1082.53",
        "rental": "0.00",
        "tax": "79.44",
        "merchant_fee": "34.78",
    },
    {
        "bank_line_id": 570,
        "date": "2026-07-14",
        "source": "Shop Boss RO 1112",
        "current_parts": "358.01",
        "target_parts": "154.84",
        "service": "179.75",
        "rental": "0.00",
        "tax": "23.42",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 579,
        "date": "2026-07-15",
        "source": "Shop Boss PS 410",
        "current_parts": "216.45",
        "target_parts": "202.29",
        "service": "0.00",
        "rental": "0.00",
        "tax": "14.16",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 600,
        "date": "2026-07-17",
        "source": "Shop Boss RO 1104 payment 2 of 2",
        "current_parts": "279.27",
        "target_parts": "113.50",
        "service": "166.27",
        "rental": "0.00",
        "tax": "0.00",
        "merchant_fee": "0.50",
    },
    {
        "bank_line_id": 602,
        "date": "2026-07-17",
        "source": "Sean Myers TX60 rental revenue; Lock Pin parts",
        "current_parts": "704.20",
        "target_parts": "4.20",
        "service": "0.00",
        "rental": "700.00",
        "tax": "0.00",
        "merchant_fee": "0.00",
    },
    {
        "bank_line_id": 612,
        "date": "2026-07-20",
        "source": "Titan TX60 rental revenue",
        "current_parts": "668.70",
        "target_parts": "0.00",
        "service": "0.00",
        "rental": "700.00",
        "tax": "0.00",
        "merchant_fee": "31.30",
    },
]


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read(models, db, uid, api_key, model, domain, fields, limit=1000, order=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def single(models, db, uid, api_key, model, domain, fields, label):
    rows = read(models, db, uid, api_key, model, domain, fields, limit=2)
    if len(rows) != 1:
        raise SystemExit(f"Expected one {label}; found {len(rows)}")
    return rows[0]


def account_domain(account_name, company_id):
    return [("name", "=", account_name), ("company_ids", "in", [company_id])]


def build_rows():
    rows = []
    for item in RECLASSES:
        current_parts = money(item["current_parts"])
        target_parts = money(item["target_parts"])
        service = money(item["service"])
        rental = money(item["rental"])
        tax = money(item["tax"])
        merchant_fee = money(item["merchant_fee"])
        parts_debit = current_parts - target_parts
        debit_total = parts_debit + merchant_fee
        credit_total = service + rental + tax
        status = "Ready" if debit_total == credit_total else "Review"
        rows.append(
            {
                **item,
                "parts_revenue_debit": str(parts_debit),
                "bank_merchant_fees_debit": str(merchant_fee),
                "service_revenue_credit": str(service),
                "rental_revenue_credit": str(rental),
                "sales_tax_payable_credit": str(tax),
                "debit_total": str(debit_total),
                "credit_total": str(credit_total),
                "status": status,
                "ref": f"Shop Boss revenue bucket reclass BSL {item['bank_line_id']}",
            }
        )
    return rows


def write_plan(rows):
    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "status",
        "bank_line_id",
        "date",
        "source",
        "current_parts",
        "target_parts",
        "parts_revenue_debit",
        "bank_merchant_fees_debit",
        "service_revenue_credit",
        "rental_revenue_credit",
        "sales_tax_payable_credit",
        "debit_total",
        "credit_total",
        "ref",
    ]
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="Post Shop Boss-backed revenue bucket reclass entries.")
    parser.add_argument("--apply", action="store_true", help="Post the reclass journal entries. Default is plan only.")
    args = parser.parse_args()

    rows = build_rows()
    write_plan(rows)

    if args.apply:
        if any(row["status"] != "Ready" for row in rows):
            raise SystemExit("Refusing to apply because at least one reclass row is not balanced.")
        load_env()
        url = os.environ["ODOO_URL"].rstrip("/")
        db = os.environ["ODOO_DB"]
        username = os.environ["ODOO_USERNAME"]
        api_key = os.environ["ODOO_API_KEY"]
        uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
        if not uid:
            raise SystemExit("Authentication failed.")
        models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
        company = single(models, db, uid, api_key, "res.company", [("name", "=", COMPANY)], ["id", "name"], COMPANY)
        journal = single(
            models,
            db,
            uid,
            api_key,
            "account.journal",
            [("name", "=", JOURNAL), ("company_id", "=", company["id"])],
            ["id", "name"],
            JOURNAL,
        )
        accounts = {
            key: single(models, db, uid, api_key, "account.account", account_domain(name, company["id"]), ["id", "name"], name)
            for key, name in ACCOUNTS.items()
        }

        for row in rows:
            existing = read(models, db, uid, api_key, "account.move", [("ref", "=", row["ref"]), ("company_id", "=", company["id"])], ["id", "name", "state"], limit=5)
            if existing:
                row["status"] = "Skipped"
                row["odoo_move"] = existing[0]["name"]
                continue

            line_ids = []
            parts_debit = money(row["parts_revenue_debit"])
            fee_debit = money(row["bank_merchant_fees_debit"])
            service_credit = money(row["service_revenue_credit"])
            rental_credit = money(row["rental_revenue_credit"])
            tax_credit = money(row["sales_tax_payable_credit"])
            label = row["source"]
            if parts_debit:
                line_ids.append((0, 0, {"account_id": accounts["parts"]["id"], "name": label, "debit": float(parts_debit), "credit": 0.0}))
            if fee_debit:
                line_ids.append((0, 0, {"account_id": accounts["fees"]["id"], "name": label, "debit": float(fee_debit), "credit": 0.0}))
            if service_credit:
                line_ids.append((0, 0, {"account_id": accounts["service"]["id"], "name": label, "debit": 0.0, "credit": float(service_credit)}))
            if rental_credit:
                line_ids.append((0, 0, {"account_id": accounts["rental"]["id"], "name": label, "debit": 0.0, "credit": float(rental_credit)}))
            if tax_credit:
                line_ids.append((0, 0, {"account_id": accounts["tax"]["id"], "name": label, "debit": 0.0, "credit": float(tax_credit)}))

            move_id = execute(
                models,
                db,
                uid,
                api_key,
                "account.move",
                "create",
                [{
                    "company_id": company["id"],
                    "journal_id": journal["id"],
                    "date": row["date"],
                    "ref": row["ref"],
                    "line_ids": line_ids,
                }],
            )
            execute(models, db, uid, api_key, "account.move", "action_post", [[move_id]])
            move = read(models, db, uid, api_key, "account.move", [("id", "=", move_id)], ["name"], limit=1)[0]
            row["status"] = "Applied"
            row["odoo_move"] = move["name"]

        write_plan(rows)

    print(f"Rows: {len(rows)}")
    print(f"Ready: {sum(1 for row in rows if row['status'] == 'Ready')}")
    print(f"Applied: {sum(1 for row in rows if row['status'] == 'Applied')}")
    print(f"Skipped: {sum(1 for row in rows if row['status'] == 'Skipped')}")
    print(f"Plan: {OUT}")


if __name__ == "__main__":
    main()
