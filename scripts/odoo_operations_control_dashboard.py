"""Create a daily full-dataset operations control snapshot from live Odoo."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from odoo_runtime import ArtifactStore, OdooClient, OdooConfig, classify_crm_rows


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
DEFAULT_OUTPUT = ROOT / "outputs" / "operations_control"


def model_exists(client: OdooClient, model: str) -> bool:
    return bool(client.count("ir.model", [("model", "=", model)]))


def safe_count(client: OdooClient, model: str, domain: list[Any]) -> int | None:
    if not model_exists(client, model):
        return None
    return client.count(model, domain)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--stale-days", type=int, default=14)
    args = parser.parse_args()

    client = OdooClient(OdooConfig.from_env(args.env_file)).connect()
    today = date.today()
    tomorrow = today + timedelta(days=1)
    stale_before = today - timedelta(days=args.stale_days)

    controls = {
        "accounting": {
            "unreconciled_bank_lines": safe_count(
                client, "account.bank.statement.line", [("is_reconciled", "=", False)]
            ),
            "draft_customer_invoices": safe_count(
                client, "account.move", [("state", "=", "draft"), ("move_type", "=", "out_invoice")]
            ),
        },
        "sales": {
            "open_quotations": safe_count(client, "sale.order", [("state", "in", ["draft", "sent"])]),
            "sales_today": safe_count(
                client,
                "sale.order",
                [("state", "in", ["sale", "done"]), ("date_order", ">=", today.isoformat()), ("date_order", "<", tomorrow.isoformat())],
            ),
        },
        "service": {
            "open_tasks": safe_count(client, "project.task", [("active", "=", True), ("stage_id.fold", "=", False)]),
        },
        "crm": {
            "actual_open_pipeline": None,
            "actual_stale_pipeline": None,
            "imported_reference_records": None,
            "overdue_activities": safe_count(
                client, "mail.activity", [("date_deadline", "<", today.isoformat())]
            ),
        },
        "product": {
            "products": safe_count(client, "product.template", []),
            "published_products": safe_count(client, "product.template", [("website_published", "=", True)]),
            "placeholder_prices": safe_count(client, "product.template", [("list_price", "<=", 1.49)]),
            "published_missing_images": safe_count(
                client, "product.template", [("website_published", "=", True), ("image_1920", "=", False)]
            ),
        },
        "automation": {
            "catalog_sync_failures": safe_count(
                client, "southern.parts.catalog.sync", [("state", "=", "failed")]
            ),
            "order_refresh_exceptions": safe_count(
                client, "southern.parts.order.refresh.queue", [("state", "=", "exception")]
            ),
        },
    }
    if model_exists(client, "crm.lead"):
        crm_fields = [
            "id",
            "active",
            "stage_id",
            "user_id",
            "partner_id",
            "email_from",
            "description",
            "activity_state",
            "probability",
            "expected_revenue",
            "create_date",
            "write_date",
        ]
        available = client.fields("crm.lead")
        crm_rows = client.search_read_all(
            "crm.lead",
            [],
            [field for field in crm_fields if field == "id" or field in available],
            context={"active_test": False},
        )
        crm_rows = classify_crm_rows(crm_rows)
        actual = [row for row in crm_rows if row["record_class"] == "actual_opportunity"]
        controls["crm"].update(
            {
                "actual_open_pipeline": sum(
                    bool(row.get("active")) and float(row.get("probability") or 0) < 100 for row in actual
                ),
                "actual_stale_pipeline": sum(
                    bool(row.get("active"))
                    and float(row.get("probability") or 0) < 100
                    and str(row.get("write_date") or "")[:10] < stale_before.isoformat()
                    for row in actual
                ),
                "imported_reference_records": sum(
                    row["record_class"] == "imported_reference" for row in crm_rows
                ),
            }
        )
    output = args.output_dir.resolve() / today.isoformat()
    manifest = ArtifactStore(output, schema_version="1.0").write_json(
        "operations_control.json",
        {"odoo_uid": client.uid, "control_date": today.isoformat(), "controls": controls},
    )
    print(
        {
            "mode": "read_only",
            "control_date": today.isoformat(),
            "sha256": manifest["sha256"],
            "output": manifest["path"],
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
