from __future__ import annotations

import json
import xmlrpc.client
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from scripts.odoo_runtime import OdooClient, OdooConfig


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUTPUT_DIR = ROOT / "outputs" / "operations"
JSON_PATH = OUTPUT_DIR / "service_operations_readiness.json"
MARKDOWN_PATH = OUTPUT_DIR / "service_operations_readiness.md"

REQUIRED_MODULES = [
    "sale_management",
    "sale_stock",
    "sale_purchase",
    "industry_fsm",
    "industry_fsm_sale",
    "industry_fsm_stock",
    "repair",
    "purchase",
    "purchase_stock",
    "purchase_repair",
    "maintenance",
    "cs_client_equipment",
    "dmc_fieldservice",
]

TARGET_MODELS = [
    "sale.order",
    "sale.order.line",
    "equipment.details",
    "equipment.jobs",
    "project.task",
    "repair.order",
    "maintenance.equipment",
    "maintenance.request",
    "purchase.order",
    "purchase.order.line",
]

LOCAL_CUSTOM_MODULES = [
    "cs_client_equipment",
    "dmc_fieldservice",
]


@dataclass(frozen=True)
class Finding:
    severity: str
    area: str
    issue: str
    evidence: str
    action: str


def read_access(client: OdooClient, model: str) -> dict[str, Any]:
    try:
        rights = bool(
            client.execute(
                model,
                "check_access_rights",
                ["read"],
                {"raise_exception": False},
            )
        )
        if not rights:
            return {"model_access": False, "record_access": False}
        client.execute(model, "search_read", [[]], {"fields": ["id"], "limit": 1})
        return {"model_access": True, "record_access": True}
    except xmlrpc.client.Fault as exc:
        return {
            "model_access": False,
            "record_access": False,
            "error": str(exc.faultString).splitlines()[0],
        }


def external_ids(client: OdooClient, model: str, res_ids: list[int]) -> dict[int, str]:
    if not res_ids:
        return {}
    rows = client.search_read_all(
        "ir.model.data",
        [("model", "=", model), ("res_id", "in", res_ids)],
        ["module", "name", "res_id"],
    )
    return {
        int(row["res_id"]): f"{row['module']}.{row['name']}"
        for row in rows
        if row.get("module") and row.get("name")
    }


def view_inventory(client: OdooClient) -> dict[str, list[dict[str, Any]]]:
    rows = client.search_read_all(
        "ir.ui.view",
        [
            ("model", "in", TARGET_MODELS),
            ("type", "in", ["list", "form", "kanban", "search"]),
            ("active", "=", True),
        ],
        ["name", "model", "type", "priority", "mode", "inherit_id"],
        order="model,type,priority,id",
    )
    ids = [int(row["id"]) for row in rows]
    xid_by_id = external_ids(client, "ir.ui.view", ids)
    result: dict[str, list[dict[str, Any]]] = {model: [] for model in TARGET_MODELS}
    for row in rows:
        item = {
            "id": row["id"],
            "xml_id": xid_by_id.get(int(row["id"]), ""),
            "name": row["name"],
            "type": row["type"],
            "priority": row.get("priority"),
            "mode": row.get("mode"),
            "inherits": row.get("inherit_id") or False,
        }
        result[row["model"]].append(item)
    return result


def model_inventory(client: OdooClient) -> dict[str, dict[str, Any]]:
    rows = client.search_read_all(
        "ir.model",
        [("model", "in", TARGET_MODELS)],
        ["name", "model", "modules"],
    )
    by_model = {row["model"]: row for row in rows}
    result: dict[str, dict[str, Any]] = {}
    for model in TARGET_MODELS:
        row = by_model.get(model, {})
        result[model] = {
            "available": bool(row),
            "name": row.get("name", ""),
            "providers": [
                value.strip()
                for value in str(row.get("modules") or "").split(",")
                if value.strip()
            ],
            "read_access": read_access(client, model) if row else {},
        }
    return result


def module_inventory(client: OdooClient) -> dict[str, dict[str, str]]:
    rows = client.search_read_all(
        "ir.module.module",
        [("name", "in", REQUIRED_MODULES)],
        ["name", "state", "installed_version"],
    )
    by_name = {row["name"]: row for row in rows}
    return {
        name: {
            "state": by_name.get(name, {}).get("state", "not_found"),
            "version": by_name.get(name, {}).get("installed_version") or "",
        }
        for name in REQUIRED_MODULES
    }


def build_findings(
    modules: dict[str, dict[str, str]],
    models: dict[str, dict[str, Any]],
    views: dict[str, list[dict[str, Any]]],
) -> list[Finding]:
    findings: list[Finding] = []
    not_installed = [name for name, row in modules.items() if row["state"] != "installed"]
    if not_installed:
        findings.append(
            Finding(
                "High",
                "Dependencies",
                "Required connector modules are not installed",
                ", ".join(not_installed),
                "Install and validate every required dependency before installing the integration add-on.",
            )
        )

    missing_models = [model for model, row in models.items() if not row["available"]]
    if missing_models:
        findings.append(
            Finding(
                "High",
                "Models",
                "Required source models are unavailable",
                ", ".join(missing_models),
                "Resolve missing modules or rename assumptions before implementation.",
            )
        )

    inaccessible = [
        model
        for model, row in models.items()
        if row["available"] and not row["read_access"].get("record_access")
    ]
    if inaccessible:
        findings.append(
            Finding(
                "High",
                "Security",
                "The audit/integration account cannot read required records",
                ", ".join(inaccessible),
                "Grant narrowly scoped read access before migration assessment and integration verification.",
            )
        )

    missing_views = [
        model
        for model in ("sale.order", "equipment.details", "project.task", "repair.order", "maintenance.request")
        if not views.get(model)
    ]
    if missing_views:
        findings.append(
            Finding(
                "High",
                "Views",
                "No active inheritable views were discovered for required models",
                ", ".join(missing_views),
                "Identify the correct installed view or defer that UI extension.",
            )
        )

    missing_local_sources = [name for name in LOCAL_CUSTOM_MODULES if not (ROOT / name).is_dir()]
    if missing_local_sources:
        findings.append(
            Finding(
                "Medium",
                "Source Control",
                "Installed custom dependency source is not present in this repository",
                ", ".join(missing_local_sources),
                "Add or otherwise pin the deployed source/version so CI and staging test the same dependency as production.",
            )
        )

    return findings


def render_markdown(report: dict[str, Any]) -> str:
    counts = Counter(row["severity"] for row in report["findings"])
    lines = [
        "# Service Implementation Readiness",
        "",
        f"Generated: {report['generated_at']}",
        f"Database: `{report['database']}`",
        "",
        "## Outcome",
        "",
        (
            f"{len(report['findings'])} findings: "
            f"{counts.get('High', 0)} high, "
            f"{counts.get('Medium', 0)} medium, "
            f"{counts.get('Low', 0)} low."
        ),
        "",
        "## Findings",
        "",
    ]
    if not report["findings"]:
        lines.append("No readiness blockers were detected.")
    for index, row in enumerate(report["findings"], start=1):
        lines.extend(
            [
                f"{index}. **[{row['severity']}] {row['area']}: {row['issue']}**",
                f"   - Evidence: {row['evidence']}",
                f"   - Required action: {row['action']}",
            ]
        )

    lines.extend(["", "## Required modules", "", "| Module | State | Version |", "|---|---|---|"])
    for name, row in report["modules"].items():
        lines.append(f"| `{name}` | {row['state']} | {row['version']} |")

    lines.extend(
        [
            "",
            "## Model and access readiness",
            "",
            "| Model | Available | Record read | Providers |",
            "|---|---:|---:|---|",
        ]
    )
    for model, row in report["models"].items():
        access = row.get("read_access", {})
        lines.append(
            f"| `{model}` | {row['available']} | "
            f"{access.get('record_access', False)} | {', '.join(row.get('providers', []))} |"
        )

    lines.extend(["", "## Candidate inherited views", ""])
    for model, rows in report["views"].items():
        lines.extend([f"### `{model}`", ""])
        if not rows:
            lines.append("- None found.")
        for row in rows:
            identity = row["xml_id"] or f"database view {row['id']}"
            lines.append(
                f"- `{identity}` — {row['type']}, {row['mode']}, "
                f"priority {row['priority']}: {row['name']}"
            )
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    client = OdooClient(OdooConfig.from_env(ENV_PATH)).connect()
    modules = module_inventory(client)
    models = model_inventory(client)
    views = view_inventory(client)
    findings = build_findings(modules, models, views)
    severity_order = {"High": 0, "Medium": 1, "Low": 2}
    findings.sort(key=lambda row: (severity_order[row.severity], row.area, row.issue))
    report = {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "database": client.config.database,
        "modules": modules,
        "models": models,
        "views": views,
        "findings": [asdict(row) for row in findings],
    }
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    JSON_PATH.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    MARKDOWN_PATH.write_text(render_markdown(report) + "\n", encoding="utf-8")
    counts = Counter(row.severity for row in findings)
    print(
        f"Findings: {len(findings)} "
        f"(High={counts['High']}, Medium={counts['Medium']}, Low={counts['Low']})"
    )
    print(f"Report: {MARKDOWN_PATH}")
    print(f"Data: {JSON_PATH}")


if __name__ == "__main__":
    main()
