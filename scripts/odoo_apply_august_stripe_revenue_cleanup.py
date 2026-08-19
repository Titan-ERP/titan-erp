from __future__ import annotations

import argparse
import csv
from decimal import Decimal
from pathlib import Path

from odoo_runtime import OdooClient, OdooConfig

ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = ROOT / "odoo_imports" / "accounting" / "stripe_reconciliation_analysis" / "2026-08-19" / "stripe_api"
AUDIT_PATH = REPORT_DIR / "august_stripe_revenue_cleanup_audit.csv"

COMPANY_ID = 2
FRT_TEMPLATE_ID = 11271
FREIGHT_CATEGORY_ID = 1336


def money(value: object) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def account_by_name(client: OdooClient, name: str) -> int:
    rows = client.execute(
        "account.account",
        "search_read",
        [[("company_ids", "in", [COMPANY_ID]), ("name", "=", name)]],
        {"fields": ["id", "name"], "limit": 1},
    )
    if not rows:
        raise RuntimeError(f"Missing account for company {COMPANY_ID}: {name}")
    return int(rows[0]["id"])


def selection_values(client: OdooClient, model: str, field: str) -> set[str]:
    fields = client.execute(model, "fields_get", [[field], ["selection"]])
    return {value for value, _label in fields[field]["selection"]}


def find_reclass_lines(client: OdooClient, old_account_id: int, new_account_id: int, issue_code: str) -> list[dict[str, object]]:
    if issue_code == "FRT_TO_PROCESSING_FEE_INCOME":
        domain = [
            ("company_id", "=", COMPANY_ID),
            ("move_id.move_type", "=", "out_invoice"),
            ("move_id.state", "=", "posted"),
            ("move_id.invoice_date", ">=", "2026-08-01"),
            ("move_id.invoice_date", "<=", "2026-08-31"),
            ("account_id", "=", old_account_id),
            "|",
            ("product_id.default_code", "=", "FRT"),
            ("name", "ilike", "FRT"),
        ]
    elif issue_code == "LABOR_TO_SERVICE_REVENUE":
        domain = [
            ("company_id", "=", COMPANY_ID),
            ("move_id.move_type", "=", "out_invoice"),
            ("move_id.state", "=", "posted"),
            ("move_id.invoice_date", ">=", "2026-08-01"),
            ("move_id.invoice_date", "<=", "2026-08-31"),
            ("account_id", "=", old_account_id),
            "|",
            ("product_id.default_code", "=", "LABOR-SHOP"),
            ("name", "ilike", "LABOR-SHOP"),
        ]
    else:
        raise ValueError(issue_code)
    rows = client.execute(
        "account.move.line",
        "search_read",
        [domain],
        {
            "fields": ["id", "date", "move_id", "name", "product_id", "account_id", "credit"],
            "order": "date, move_id, id",
            "limit": 500,
        },
    )
    return [
        {
            "issue_code": issue_code,
            "source_line_id": row["id"],
            "invoice": row["move_id"][1],
            "date": row["date"],
            "name": row.get("name") or "",
            "old_account_id": old_account_id,
            "new_account_id": new_account_id,
            "amount": money(row["credit"]),
        }
        for row in rows
        if money(row["credit"]) > 0
    ]


def already_posted(client: OdooClient, ref: str) -> bool:
    return bool(client.execute("account.move", "search_count", [[("company_id", "=", COMPANY_ID), ("ref", "=", ref)]]))


def create_reclass_move(client: OdooClient, item: dict[str, object]) -> int | None:
    ref = f"August Stripe revenue cleanup {item['issue_code']} AML {item['source_line_id']}"
    if already_posted(client, ref):
        return None
    amount = float(item["amount"])
    move_id = client.execute(
        "account.move",
        "create",
        [
            {
                "company_id": COMPANY_ID,
                "date": str(item["date"]),
                "ref": ref,
                "move_type": "entry",
                "line_ids": [
                    (
                        0,
                        0,
                        {
                            "name": f"{item['issue_code']} from {item['invoice']}",
                            "account_id": item["old_account_id"],
                            "debit": amount,
                            "credit": 0.0,
                        },
                    ),
                    (
                        0,
                        0,
                        {
                            "name": f"{item['issue_code']} from {item['invoice']}",
                            "account_id": item["new_account_id"],
                            "debit": 0.0,
                            "credit": amount,
                        },
                    ),
                ],
            }
        ],
    )
    client.execute("account.move", "action_post", [[move_id]])
    return int(move_id)


def write_audit(rows: list[dict[str, object]]) -> None:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "mode",
        "issue_code",
        "source_line_id",
        "invoice",
        "date",
        "amount",
        "old_account_id",
        "new_account_id",
        "result",
        "move_id",
    ]
    with AUDIT_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Fix August Stripe-linked Southern revenue bucket drift.")
    parser.add_argument(
        "--env",
        type=Path,
        default=ROOT / "odoo_connection.env",
        help="Path to Odoo connection env file. If missing, existing process environment is used.",
    )
    parser.add_argument("--apply", action="store_true", help="Perform Odoo writes. Default is dry-run.")
    args = parser.parse_args()

    client = OdooClient(OdooConfig.from_env(args.env if args.env.exists() else None)).connect()
    freight_available = "freight" in selection_values(client, "product.template", "southern_revenue_bucket")

    accounts = {
        "freight": account_by_name(client, "Shipping / Freight Revenue"),
        "freight_expense": account_by_name(client, "Freight Expense"),
        "processing_fee": account_by_name(client, "Transaction Processing Fee Income"),
        "parts": account_by_name(client, "Parts Revenue"),
        "service": account_by_name(client, "Service Revenue"),
    }

    items: list[dict[str, object]] = []
    items.extend(find_reclass_lines(client, accounts["processing_fee"], accounts["freight"], "FRT_TO_PROCESSING_FEE_INCOME"))
    items.extend(find_reclass_lines(client, accounts["parts"], accounts["service"], "LABOR_TO_SERVICE_REVENUE"))

    audit: list[dict[str, object]] = []
    if args.apply and freight_available:
        client.execute(
            "product.template",
            "write",
            [
                [FRT_TEMPLATE_ID],
                {
                    "southern_revenue_bucket": "freight",
                    "property_account_income_id": accounts["freight"],
                    "southern_accounting_review_note": (
                        "August 2026 Stripe cleanup: FRT is freight revenue, not card processing fee income."
                    ),
                },
            ],
        )
        client.execute(
            "product.category",
            "write",
            [
                [FREIGHT_CATEGORY_ID],
                {
                    "southern_accounting_bucket": "freight",
                    "property_account_income_categ_id": accounts["freight"],
                    "property_account_expense_categ_id": accounts["freight_expense"],
                    "southern_accounting_review_note": (
                        "August 2026 Stripe cleanup: freight/hauling charges map to Shipping / Freight Revenue."
                    ),
                },
            ],
        )

    for item in items:
        result = "dry_run"
        move_id = ""
        if args.apply:
            if not freight_available:
                result = "blocked_missing_freight_selection"
            else:
                created = create_reclass_move(client, item)
                result = "already_posted" if created is None else "posted"
                move_id = created or ""
        audit.append(
            {
                "mode": "apply" if args.apply else "dry_run",
                "issue_code": item["issue_code"],
                "source_line_id": item["source_line_id"],
                "invoice": item["invoice"],
                "date": item["date"],
                "amount": item["amount"],
                "old_account_id": item["old_account_id"],
                "new_account_id": item["new_account_id"],
                "result": result,
                "move_id": move_id,
            }
        )

    write_audit(audit)
    totals: dict[str, Decimal] = {}
    for item in items:
        totals[item["issue_code"]] = totals.get(item["issue_code"], Decimal("0.00")) + item["amount"]
    print(f"mode={'apply' if args.apply else 'dry_run'}")
    print(f"freight_selection_available={freight_available}")
    for key, value in sorted(totals.items()):
        print(f"{key}={value}")
    print(f"items={len(items)}")
    print(f"audit={AUDIT_PATH}")
    if args.apply and not freight_available:
        raise SystemExit("Blocked: deploy/upgrade southern_accounting_guardrails with freight bucket before applying.")


if __name__ == "__main__":
    main()
