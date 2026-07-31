"""Record external product-worker run state in Odoo.

The command is read-only unless the shared supervised write gate is satisfied.
It is intentionally separate from crawler/worker implementations so AWS,
Codex, and manual workers can all use the same Odoo ledger.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from odoo_runtime import ApplyGate, OdooClient, OdooConfig
from odoo_runtime.safety import append_audit

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
AUDIT_PATH = ROOT / "outputs" / "write_audit" / "odoo_writes.jsonl"
WORKFLOW = "product-automation-run-ledger"


def common_values(args: argparse.Namespace) -> dict:
    values = {
        "name": args.name,
        "external_run_id": args.external_run_id,
        "command_id": args.command_id,
        "idempotency_key": getattr(args, "idempotency_key", None),
        "worker": args.worker,
        "mode": args.mode,
        "free_gb": args.free_gb,
        "requested_count": args.requested_count,
        "processed_count": args.processed_count,
        "changed_count": args.changed_count,
        "error_count": args.error_count,
        "http_request_count": args.http_request_count,
        "slow_page_count": args.slow_page_count,
        "artifact_uri": args.artifact_uri,
        "artifact_sha256": args.artifact_sha256,
        "artifact_schema_version": args.artifact_schema_version,
        "archive_uri": args.archive_uri,
        "artifact_archived": args.archive_verified,
        "evidence_summary": args.evidence_summary,
        "error_message": args.error_message,
    }
    return {key: value for key, value in values.items() if value not in (None, "")}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=ENV_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=1)
    subparsers = parser.add_subparsers(dest="operation", required=True)

    start = subparsers.add_parser("start", help="Begin an external automation run.")
    start.add_argument("--sync-id", type=int, required=True)
    start.add_argument("--name", default="External Product Automation Run")
    start.add_argument("--external-run-id")
    start.add_argument("--command-id")
    start.add_argument(
        "--idempotency-key",
        help="Stable logical run key. Defaults to the shared gate hash of the start payload.",
    )
    start.add_argument("--worker", choices=("odoo", "aws", "codex", "manual"), default="manual")
    start.add_argument(
        "--mode",
        choices=("dry_run", "evidence_only", "maintenance", "apply"),
        default="dry_run",
    )
    start.add_argument("--free-gb", type=float, required=True)
    start.add_argument("--requested-count", type=int, default=0)
    start.add_argument("--processed-count", type=int, default=0)
    start.add_argument("--changed-count", type=int, default=0)
    start.add_argument("--error-count", type=int, default=0)
    start.add_argument("--http-request-count", type=int, default=0)
    start.add_argument("--slow-page-count", type=int, default=0)
    start.add_argument("--artifact-uri")
    start.add_argument("--artifact-sha256")
    start.add_argument("--artifact-schema-version", default="1.0")
    start.add_argument("--archive-uri")
    start.add_argument("--archive-verified", action="store_true")
    start.add_argument("--evidence-summary")
    start.add_argument("--error-message")

    finish = subparsers.add_parser("finish", help="Finish an existing automation run.")
    finish.add_argument("--run-id", type=int, required=True)
    finish.add_argument(
        "--state",
        choices=("succeeded", "blocked", "failed", "cancelled"),
        required=True,
    )
    finish.add_argument("--name")
    finish.add_argument("--external-run-id")
    finish.add_argument("--command-id")
    finish.add_argument("--worker", choices=("odoo", "aws", "codex", "manual"))
    finish.add_argument("--mode", choices=("dry_run", "evidence_only", "maintenance", "apply"))
    finish.add_argument("--free-gb", type=float)
    finish.add_argument("--requested-count", type=int)
    finish.add_argument("--processed-count", type=int)
    finish.add_argument("--changed-count", type=int)
    finish.add_argument("--error-count", type=int)
    finish.add_argument("--http-request-count", type=int)
    finish.add_argument("--slow-page-count", type=int)
    finish.add_argument("--artifact-uri")
    finish.add_argument("--artifact-sha256")
    finish.add_argument("--artifact-schema-version")
    finish.add_argument("--archive-uri")
    finish.add_argument("--archive-verified", action="store_true")
    finish.add_argument("--evidence-summary")
    finish.add_argument("--error-message")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    client = OdooClient(OdooConfig.from_env(args.env_file)).connect()
    if not client.count("ir.model", [("model", "=", "southern.parts.automation.run")]):
        raise RuntimeError(
            "southern.parts.automation.run is not installed; upgrade Southern Parts Intelligence first."
        )
    payload = {
        "operation": args.operation,
        "sync_id": getattr(args, "sync_id", None),
        "run_id": getattr(args, "run_id", None),
        "state": getattr(args, "state", None),
        "values": common_values(args),
    }
    if not args.apply:
        print({"mode": "dry_run", **payload})
        return 0
    gate = ApplyGate(
        WORKFLOW,
        True,
        args.confirm,
        args.reason,
        args.max_records,
    )
    gate.authorize(1)
    if args.operation == "start":
        if not payload["values"].get("idempotency_key"):
            payload["values"]["idempotency_key"] = gate.idempotency_key(payload)
        run_id = client.call(
            "southern.parts.automation.run",
            "begin_external_run",
            sync_id=args.sync_id,
            values=payload["values"],
        )
        result = {"run_id": run_id, "state": "running"}
    else:
        client.call(
            "southern.parts.automation.run",
            "finish_run",
            ids=[args.run_id],
            state=args.state,
            values=payload["values"],
        )
        result = {"run_id": args.run_id, "state": args.state}
    append_audit(AUDIT_PATH, gate.audit_row(payload, 1))
    print({"mode": "apply", **result})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
