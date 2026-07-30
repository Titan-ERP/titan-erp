from __future__ import annotations

import json
import xmlrpc.client
from collections import Counter
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts.odoo_runtime import OdooClient, OdooConfig


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUTPUT_DIR = ROOT / "outputs" / "operations"
JSON_PATH = OUTPUT_DIR / "sales_service_purchase_audit.json"
MARKDOWN_PATH = OUTPUT_DIR / "sales_service_purchase_audit.md"


@dataclass(frozen=True)
class Finding:
    severity: str
    area: str
    issue: str
    evidence: str
    recommendation: str


class Audit:
    def __init__(self, client: OdooClient):
        self.client = client
        self.field_cache: dict[str, dict[str, Any]] = {}
        self.findings: list[Finding] = []
        self.metrics: dict[str, Any] = {}

    def model_exists(self, model: str) -> bool:
        return bool(self.client.count("ir.model", [("model", "=", model)]))

    def fields(self, model: str) -> dict[str, Any]:
        if model not in self.field_cache:
            self.field_cache[model] = self.client.fields(model) if self.model_exists(model) else {}
        return self.field_cache[model]

    def has(self, model: str, field: str) -> bool:
        return field in self.fields(model)

    def count(self, model: str, domain: list[Any] | None = None) -> int:
        return self.client.count(model, domain or [])

    def add(
        self,
        severity: str,
        area: str,
        issue: str,
        evidence: str,
        recommendation: str,
    ) -> None:
        self.findings.append(Finding(severity, area, issue, evidence, recommendation))

    def installed_modules(self) -> None:
        names = [
            "sale_management",
            "sale_stock",
            "sale_purchase",
            "repair",
            "purchase_repair",
            "industry_fsm",
            "industry_fsm_sale",
            "industry_fsm_stock",
            "purchase",
            "purchase_stock",
            "stock",
            "maintenance",
            "cs_client_equipment",
            "dmc_fieldservice",
            "cs_rental_inspection",
            "southern_parts_intelligence",
        ]
        rows = self.client.search_read_all(
            "ir.module.module",
            [("name", "in", names)],
            ["name", "state", "installed_version"],
        )
        by_name = {row["name"]: row for row in rows}
        self.metrics["modules"] = {
            name: {
                "state": by_name.get(name, {}).get("state", "not_found"),
                "version": by_name.get(name, {}).get("installed_version") or "",
            }
            for name in names
        }
        missing = [
            name
            for name in ("sale_purchase", "repair", "industry_fsm_sale", "purchase_stock")
            if self.metrics["modules"][name]["state"] != "installed"
        ]
        if missing:
            self.add(
                "High",
                "Architecture",
                "Core cross-module connectors are not all installed",
                f"Not installed: {', '.join(missing)}.",
                "Install or validate the connector modules before adding custom duplication of native Odoo links.",
            )

    def model_field_metadata(self, model: str) -> dict[str, dict[str, Any]]:
        rows = self.client.search_read_all(
            "ir.model.fields",
            [("model", "=", model)],
            ["name", "field_description", "ttype", "relation", "required"],
        )
        return {row["name"]: row for row in rows}

    def equipment(self) -> None:
        client_model = "equipment.details"
        job_model = "equipment.jobs"
        client_fields = self.model_field_metadata(client_model)
        job_fields = self.model_field_metadata(job_model)
        metrics: dict[str, Any] = {
            "customer_model": client_model,
            "customer_model_available": bool(client_fields),
            "client_field": client_fields.get("client", {}),
            "serial_field": client_fields.get("serial_no", {}),
            "job_task_field": job_fields.get("task_id", {}),
        }

        try:
            metrics["customer_equipment_access"] = True
            metrics["customer_equipment_total"] = self.count(client_model)
            if "client" in client_fields:
                metrics["customer_equipment_without_client"] = self.count(
                    client_model,
                    [("client", "=", False)],
                )
            if "serial_no" in client_fields:
                metrics["customer_equipment_without_serial"] = self.count(
                    client_model,
                    [("serial_no", "=", False)],
                )
            metrics["equipment_jobs_total"] = self.count(job_model) if job_fields else 0
            if "equipment" in job_fields:
                metrics["equipment_jobs_without_equipment"] = self.count(
                    job_model,
                    [("equipment", "=", False)],
                )
        except xmlrpc.client.Fault as exc:
            metrics["customer_equipment_access"] = False
            metrics["access_error"] = str(exc.faultString).splitlines()[0]

        maintenance_equipment = "maintenance.equipment"
        maintenance_request = "maintenance.request"
        maintenance_equipment_fields = self.fields(maintenance_equipment)
        maintenance_request_fields = self.fields(maintenance_request)
        metrics["internal_maintenance"] = {
            "equipment_total": self.count(maintenance_equipment) if maintenance_equipment_fields else 0,
            "request_total": self.count(maintenance_request) if maintenance_request_fields else 0,
        }
        if maintenance_equipment_fields:
            for field in ("maintenance_team_id", "owner_user_id", "location_id", "serial_no"):
                if field in maintenance_equipment_fields:
                    metrics["internal_maintenance"][f"equipment_without_{field}"] = self.count(
                        maintenance_equipment,
                        [(field, "=", False)],
                    )
        if maintenance_request_fields:
            for field in ("equipment_id", "maintenance_team_id", "owner_user_id"):
                if field in maintenance_request_fields:
                    metrics["internal_maintenance"][f"requests_without_{field}"] = self.count(
                        maintenance_request,
                        [(field, "=", False)],
                    )
        self.metrics["equipment"] = metrics

        if not client_fields:
            self.add(
                "High",
                "Client Equipment",
                "No customer-equipment master is available",
                "equipment.details metadata was not found.",
                "Install or restore Client Equipment before linking service history.",
            )
        else:
            client_field = client_fields.get("client")
            if client_field and not client_field.get("required"):
                self.add(
                    "High",
                    "Client Equipment",
                    "Customer ownership is optional on customer equipment",
                    "equipment.details.client is a non-required many2one to res.partner.",
                    "Require a client before equipment becomes active or can be scheduled for customer work.",
                )
            if not metrics.get("customer_equipment_access"):
                self.add(
                    "High",
                    "Client Equipment",
                    "The audit/integration account cannot validate customer equipment",
                    metrics.get("access_error", "Read access is denied."),
                    "Grant Client Equipment read access before migration or automated cross-module linking.",
                )
            elif metrics.get("customer_equipment_without_client"):
                self.add(
                    "High",
                    "Client Equipment",
                    "Customer equipment records lack a linked Contact",
                    f"{metrics['customer_equipment_without_client']} records have no client.",
                    "Resolve missing owners before enforcing the customer/equipment domain on service work.",
                )

        task_field = job_fields.get("task_id")
        if task_field and task_field.get("ttype") != "many2one":
            self.add(
                "High",
                "Client Equipment → Field Service",
                "Equipment Job task references are not relational",
                f"equipment.jobs.task_id is type {task_field.get('ttype')}, not many2one to project.task.",
                "Add a real project_task_id relation, migrate validated IDs, and retire the integer field.",
            )

        internal = metrics["internal_maintenance"]
        if internal["equipment_total"] == 0 and internal["request_total"] == 0:
            self.add(
                "Medium",
                "Maintenance",
                "Internal Maintenance is installed but not operating",
                "0 maintenance equipment records and 0 maintenance requests exist.",
                "Load active internal assets and preventive plans only after customer/internal ownership rules are approved.",
            )

    def sales(self) -> None:
        model = "sale.order"
        fields = self.fields(model)
        cutoff = (date.today() - timedelta(days=30)).isoformat()
        metrics = {
            "total": self.count(model),
            "quotations": self.count(model, [("state", "in", ["draft", "sent"])]),
            "confirmed": self.count(model, [("state", "in", ["sale", "done"])]),
            "stale_quotations": self.count(
                model,
                [("state", "in", ["draft", "sent"]), ("date_order", "<", cutoff)],
            ),
        }
        if "invoice_status" in fields:
            metrics["to_invoice"] = self.count(
                model,
                [("state", "in", ["sale", "done"]), ("invoice_status", "=", "to invoice")],
            )
        if "user_id" in fields:
            metrics["without_salesperson"] = self.count(
                model,
                [("state", "!=", "cancel"), ("user_id", "=", False)],
            )
        self.metrics["sales"] = metrics

        if metrics["stale_quotations"]:
            self.add(
                "Medium",
                "Sales",
                "Stale quotations obscure the active pipeline",
                f"{metrics['stale_quotations']} draft/sent quotations are older than 30 days.",
                "Require a next activity or explicit close/lost reason and add a weekly stale-quotation review.",
            )
        if metrics.get("to_invoice"):
            self.add(
                "High",
                "Sales",
                "Confirmed orders are waiting for invoice action",
                f"{metrics['to_invoice']} confirmed/done sales orders have invoice_status = to invoice.",
                "Separate delivered work ready to invoice from orders blocked by missing delivery or service completion.",
            )
        if metrics.get("without_salesperson"):
            self.add(
                "Medium",
                "Sales",
                "Open sales documents lack ownership",
                f"{metrics['without_salesperson']} non-cancelled orders or quotations have no salesperson.",
                "Default the salesperson from customer/team and require ownership before quotation confirmation.",
            )

    def field_service(self) -> None:
        model = "project.task"
        fields = self.fields(model)
        if not fields:
            self.metrics["field_service"] = {"available": False}
            self.add(
                "High",
                "Field Service",
                "Field Service is unavailable",
                "project.task is not accessible.",
                "Install and configure Project/Field Service before implementing service handoffs.",
            )
            return

        domain = [("is_fsm", "=", True)] if "is_fsm" in fields else []
        metrics: dict[str, Any] = {
            "available": True,
            "total": self.count(model, domain),
        }
        if "user_ids" in fields:
            metrics["unassigned"] = self.count(model, domain + [("user_ids", "=", False)])
        if "partner_id" in fields:
            metrics["without_customer"] = self.count(model, domain + [("partner_id", "=", False)])
        if "date_deadline" in fields:
            metrics["past_due"] = self.count(
                model,
                domain + [("date_deadline", "<", date.today().isoformat())],
            )
        sale_link = next((name for name in ("sale_order_id", "sale_line_id") if name in fields), None)
        if sale_link:
            metrics["sale_link_field"] = sale_link
            metrics["without_sale_link"] = self.count(model, domain + [(sale_link, "=", False)])
        equipment_fields = [
            name for name in ("dmc_equipment", "dmc_serial_number", "dmc_equipment_run_hours")
            if name in fields
        ]
        metrics["equipment_fields"] = equipment_fields
        for field in equipment_fields:
            metrics[f"missing_{field}"] = self.count(model, domain + [(field, "=", False)])
        self.metrics["field_service"] = metrics

        if metrics.get("unassigned"):
            self.add(
                "High",
                "Field Service",
                "Dispatch work can sit without an owner",
                f"{metrics['unassigned']} field-service tasks are unassigned.",
                "Require an assignee before a task enters Planned or In Progress.",
            )
        if metrics.get("past_due"):
            self.add(
                "Medium",
                "Field Service",
                "Past-due tasks need dispatch review",
                f"{metrics['past_due']} field-service tasks are past their deadline.",
                "Add a daily dispatcher view for overdue and unscheduled work.",
            )
        if metrics.get("without_sale_link"):
            total = metrics["total"]
            count = metrics["without_sale_link"]
            self.add(
                "High" if total and count / total >= 0.5 else "Medium",
                "Sales → Field Service",
                "Service tasks are disconnected from commercial records",
                f"{count} of {total} field-service tasks have no {metrics['sale_link_field']}.",
                "Create service tasks from sales lines or require an explicit non-billable/internal-work reason.",
            )
        missing_equipment = max(
            (metrics.get(f"missing_{field}", 0) for field in equipment_fields),
            default=0,
        )
        if metrics["total"] and (not equipment_fields or missing_equipment):
            self.add(
                "High",
                "Field Service",
                "Equipment identity is incomplete or stored only as free text",
                (
                    "The structured custom equipment fields are not installed."
                    if not equipment_fields
                    else f"Up to {missing_equipment} field-service tasks lack equipment identity values."
                ),
                "Link tasks to a reusable equipment/asset record with customer, serial, model, and service history.",
            )

    def repairs(self) -> None:
        model = "repair.order"
        fields = self.fields(model)
        if not fields:
            self.metrics["repairs"] = {"available": False}
            self.add(
                "High",
                "Repairs",
                "Repair workflow is unavailable or inaccessible",
                "repair.order is not available through the live API.",
                "Install/authorize Repairs and define when work belongs in Repair versus Field Service.",
            )
            return

        metrics: dict[str, Any] = {"available": True, "total": self.count(model)}
        if "state" in fields:
            rows = self.client.execute(
                model,
                "read_group",
                [[]],
                {"fields": ["state"], "groupby": ["state"], "lazy": False},
            )
            metrics["states"] = {
                str(row.get("state") or "blank"): row.get("state_count", row.get("__count", 0))
                for row in rows
            }
        for key, candidates in {
            "customer": ("partner_id",),
            "product": ("product_id",),
            "serial": ("lot_id", "lot_id"),
            "sale": ("sale_order_id", "sale_order_line_id"),
            "task": ("task_id", "project_task_id"),
            "responsible": ("user_id",),
        }.items():
            field = next((candidate for candidate in candidates if candidate in fields), None)
            if field:
                metrics[f"{key}_field"] = field
                metrics[f"without_{key}"] = self.count(model, [(field, "=", False)])
        self.metrics["repairs"] = metrics

        if metrics.get("without_customer"):
            self.add(
                "High",
                "Repairs",
                "Repair orders lack customers",
                f"{metrics['without_customer']} repair orders have no customer.",
                "Require the customer and equipment/serial identity before repair intake.",
            )
        if metrics.get("without_serial"):
            self.add(
                "High",
                "Repairs",
                "Repair history cannot reliably follow the physical unit",
                f"{metrics['without_serial']} repair orders have no serial/lot.",
                "Require a serial/asset link for serialized equipment, with a documented exception for bench parts.",
            )
        if metrics.get("without_sale"):
            self.add(
                "Medium",
                "Sales → Repairs",
                "Repair work is disconnected from its commercial record",
                f"{metrics['without_sale']} repair orders have no {metrics['sale_field']}.",
                "Create quotations from repair estimates or link the originating sales document.",
            )
        if metrics["total"] == 0:
            self.add(
                "High",
                "Repairs",
                "The Repairs app is installed but unused",
                "0 repair orders exist while service work is present in Field Service.",
                "Define an intake rule: shop/bench work in Repairs, on-site work in Field Service, with shared equipment history.",
            )

    def purchasing(self) -> None:
        model = "purchase.order"
        fields = self.fields(model)
        metrics: dict[str, Any] = {
            "total": self.count(model),
            "rfqs": self.count(model, [("state", "in", ["draft", "sent", "to approve"])]),
            "confirmed": self.count(model, [("state", "in", ["purchase", "done"])]),
        }
        if "user_id" in fields:
            metrics["without_buyer"] = self.count(
                model,
                [("state", "!=", "cancel"), ("user_id", "=", False)],
            )
        if "origin" in fields:
            metrics["without_origin"] = self.count(
                model,
                [("state", "in", ["purchase", "done"]), ("origin", "=", False)],
            )
        if "invoice_status" in fields:
            metrics["to_bill"] = self.count(
                model,
                [("state", "in", ["purchase", "done"]), ("invoice_status", "=", "to invoice")],
            )

        line_model = "purchase.order.line"
        line_fields = self.fields(line_model)
        sale_link = next((name for name in ("sale_line_id", "sale_order_id") if name in line_fields), None)
        metrics["sale_link_field"] = sale_link or ""
        if sale_link:
            metrics["sale_linked_lines"] = self.count(line_model, [(sale_link, "!=", False)])
        self.metrics["purchase"] = metrics

        if metrics.get("without_buyer"):
            self.add(
                "Medium",
                "Purchase",
                "Open purchasing documents lack ownership",
                f"{metrics['without_buyer']} non-cancelled RFQs/POs have no buyer.",
                "Default a buyer by vendor or product category and require ownership before confirmation.",
            )
        if metrics.get("without_origin"):
            self.add(
                "Medium",
                "Service/Sales → Purchase",
                "Purchase orders lack a traceable demand source",
                f"{metrics['without_origin']} confirmed/done POs have no origin.",
                "Populate source document links for sales, repair, field-service, or replenishment demand.",
            )
        if metrics.get("to_bill"):
            self.add(
                "Medium",
                "Purchase",
                "Received or ordered purchases await vendor billing action",
                f"{metrics['to_bill']} confirmed/done POs are ready to bill.",
                "Add an AP queue that distinguishes missing vendor bills from receipt discrepancies.",
            )
        if metrics["confirmed"] and not metrics.get("sale_linked_lines"):
            self.add(
                "High",
                "Sales/Service → Purchase",
                "Purchasing is not linked at line level to customer demand",
                f"{metrics['confirmed']} confirmed/done POs exist, but no purchase line has a sales-demand link.",
                "Use buy/MTO routes for demand-driven items and add explicit repair/task demand references for service parts.",
            )

    def run(self) -> dict[str, Any]:
        self.installed_modules()
        self.equipment()
        self.sales()
        self.field_service()
        self.repairs()
        self.purchasing()
        severity_order = {"High": 0, "Medium": 1, "Low": 2}
        self.findings.sort(key=lambda item: (severity_order[item.severity], item.area, item.issue))
        return {
            "schema_version": "1.0",
            "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "database": self.client.config.database,
            "metrics": self.metrics,
            "findings": [asdict(item) for item in self.findings],
        }


def render_markdown(report: dict[str, Any]) -> str:
    severity_counts = Counter(row["severity"] for row in report["findings"])
    lines = [
        "# Sales, Repair, Field Service, and Purchase Audit",
        "",
        f"Generated: {report['generated_at']}",
        f"Database: `{report['database']}`",
        "",
        "## Outcome",
        "",
        (
            f"{len(report['findings'])} findings: "
            f"{severity_counts.get('High', 0)} high, "
            f"{severity_counts.get('Medium', 0)} medium, "
            f"{severity_counts.get('Low', 0)} low."
        ),
        "",
        "## Metrics",
        "",
        "```json",
        json.dumps(report["metrics"], indent=2, sort_keys=True),
        "```",
        "",
        "## Findings",
        "",
    ]
    if not report["findings"]:
        lines.append("No cross-module inefficiencies were detected.")
    for index, finding in enumerate(report["findings"], start=1):
        lines.extend(
            [
                f"{index}. **[{finding['severity']}] {finding['area']}: {finding['issue']}**",
                f"   - Evidence: {finding['evidence']}",
                f"   - Improvement: {finding['recommendation']}",
            ]
        )
    lines.extend(
        [
            "",
            "## Recommended implementation sequence",
            "",
            "1. Establish one structured equipment/asset identity shared by Field Service and Repairs.",
            "2. Enforce dispatch readiness (customer, equipment, assignee, schedule) at workflow transitions.",
            "3. Link billable service and repair work to sales documents and invoice readiness.",
            "4. Link demand-driven purchase lines to the originating sale, repair, or field-service task.",
            "5. Add role-based operational queues for stale quotes, dispatch exceptions, repair intake, and PO follow-up.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    client = OdooClient(OdooConfig.from_env(ENV_PATH)).connect()
    report = Audit(client).run()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(report), encoding="utf-8")
    counts = Counter(row["severity"] for row in report["findings"])
    print(f"Connected uid: {client.uid}")
    print(f"Findings: {len(report['findings'])} (High={counts['High']}, Medium={counts['Medium']}, Low={counts['Low']})")
    print(f"Report: {MARKDOWN_PATH}")
    print(f"Data: {JSON_PATH}")


if __name__ == "__main__":
    main()
