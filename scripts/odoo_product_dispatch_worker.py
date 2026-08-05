"""Claim one Odoo-owned product job and execute it on the external worker."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any

from scripts.odoo_runtime import OdooClient, OdooConfig

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "odoo_connection.env"
DEFAULT_ARTIFACT_ROOT = ROOT / "outputs" / "product-dispatch"
SUPPORTED_JOBS = ("sparex_discovery", "catalog_release")
MAX_PORTAL_LIMIT = 5
MAX_RELEASE_LIMIT = 50
WARNING_PATTERNS = (
    r"portal_",
    r"html_proxy_error",
    r"\bhttp[_ ]?(?:429|500|502|503|504)\b",
    r"\b(?:429|500|502|503|504)\b",
    r"timeout",
    r"timed out",
    r"login(?:[_ ]failed| issue| error)",
    r"slow page",
    r"abnormal slowness",
    r"odoo transient",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--dealer-env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--s3-bucket", default=os.environ.get("SOUTHERN_PRODUCT_ARTIFACT_BUCKET", ""))
    return parser


def _bounded_request(request: dict[str, Any]) -> tuple[int, int, float]:
    portal_limit = max(1, min(int(request.get("limit") or MAX_PORTAL_LIMIT), MAX_PORTAL_LIMIT))
    release_limit = max(
        1,
        min(int(request.get("release_limit") or portal_limit), MAX_RELEASE_LIMIT),
    )
    throttle = max(3.0, float(request.get("throttle_seconds") or 3.0))
    if int(request.get("http_retries") or 0) != 0:
        raise RuntimeError("Product dispatches must disable HTTP retries.")
    return portal_limit, release_limit, throttle


def build_job_command(
    claim: dict[str, Any],
    *,
    python: str,
    odoo_env_file: Path,
    dealer_env_file: Path,
    artifact_root: Path,
    worker_id: str,
    s3_bucket: str,
) -> list[str]:
    request = dict(claim.get("request") or {})
    portal_limit, release_limit, throttle = _bounded_request(request)
    common = [
        "--odoo-env-file",
        str(odoo_env_file),
        "--dealer-env-file",
        str(dealer_env_file),
        "--artifact-root",
        str(artifact_root),
        "--worker-id",
        worker_id,
        "--s3-bucket",
        s3_bucket,
    ]
    if claim.get("job_type") == "sparex_discovery":
        run_key = request.get("run_key") or os.environ.get(
            "SPAREX_DISCOVERY_RUN_KEY", "sparex-full-catalog-inventory-v3"
        )
        command = [
            python,
            "-m",
            "scripts.sparex_catalog_discovery",
            *common,
            "--run-key",
            str(run_key),
            "--max-pages-per-checkpoint",
            str(portal_limit),
            "--throttle-seconds",
            str(throttle),
            "--apply",
            "--confirm",
            "sparex-discovery-queue",
            "--reason",
            "Odoo-dispatched throttled Sparex evidence checkpoint",
        ]
        if request.get("create_missing_products"):
            command.append("--create-missing-products")
        return command
    if claim.get("job_type") == "catalog_release":
        if claim.get("mode") != "apply" or not request.get("publish"):
            raise RuntimeError("Catalog release dispatch is missing Odoo apply approval.")
        return [
            python,
            "-m",
            "scripts.sparex_catalog_agents.orchestrator",
            *common,
            "--limit",
            str(release_limit),
            "--cost-recovery-limit",
            str(portal_limit),
            "--source-repair-limit",
            str(portal_limit),
            "--skip-quote-publication",
            "--throttle-seconds",
            str(throttle),
            "--apply",
            "--publish",
            "--confirm",
            "catalog-agent-automation",
            "--reason",
            "Odoo-approved catalog update and website publication",
        ]
    raise RuntimeError("Unsupported Odoo product dispatch job type.")


def resolve_discovery_run_key(client: OdooClient, base_key: str, dispatch_run_id: int) -> str:
    rows = client.call(
        "southern.sparex.discovery.run",
        "search_read",
        domain=[
            "|",
            ("idempotency_key", "=", base_key),
            ("idempotency_key", "=like", f"{base_key}-cycle-%"),
        ],
        fields=["idempotency_key", "state"],
        limit=1,
        order="id desc",
    )
    if not rows:
        return base_key
    latest = rows[0]
    if latest.get("state") not in {"completed", "failed", "cancelled"}:
        return str(latest["idempotency_key"])
    return f"{base_key}-cycle-{int(dispatch_run_id)}"


def _last_json(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    return {}


def warning_cooldown_minutes(output: str) -> int:
    normalized = (output or "").casefold()
    return 60 if any(re.search(pattern, normalized) for pattern in WARNING_PATTERNS) else 0


def result_cooldown_minutes(result: dict[str, Any]) -> int:
    """Inspect only explicit warning fields in a successful structured result."""
    cost_recovery = dict(result.get("cost_recovery") or {})
    if cost_recovery.get("state") == "portal_cooldown" or cost_recovery.get("write_blocked") is True:
        return 60
    warning_text = " ".join(
        str(result.get(key) or "")
        for key in ("error", "error_code", "warning", "warning_code")
    )
    return warning_cooldown_minutes(warning_text)


def finish_values(result: dict[str, Any]) -> dict[str, Any]:
    artifact_uri = result.get("result_uri") or ""
    cost_recovery = dict(result.get("cost_recovery") or {})
    discovery_changed = int(result.get("corrected") or 0) + int(result.get("created_count") or 0)
    http_requests = int(
        result.get("http_requests")
        if result.get("http_requests") is not None
        else result.get("pages_processed") or cost_recovery.get("http_requests") or 0
    )
    slow_pages = int(result.get("slow_pages") or cost_recovery.get("slow_pages") or 0)
    return {
        "processed_count": int(
            result.get("pages_processed") or cost_recovery.get("claimed") or result.get("prepared") or 0
        ),
        "changed_count": int(
            discovery_changed
            or cost_recovery.get("applied")
            or result.get("source_linked")
            or result.get("published")
            or 0
        ),
        "error_count": int(bool(result.get("failed"))),
        "http_request_count": http_requests,
        "slow_page_count": slow_pages,
        "artifact_uri": artifact_uri,
        "artifact_sha256": result.get("result_sha256") or "",
        "artifact_schema_version": "1.1",
        "archive_uri": artifact_uri,
        "artifact_archived": bool(artifact_uri.startswith("s3://")),
        "evidence_summary": json.dumps(
            {
                key: result.get(key)
                for key in (
                    "state",
                    "pages_processed",
                    "observed",
                    "corrected",
                    "created_count",
                    "prepared",
                    "published",
                    "http_requests",
                    "slow_pages",
                    "http_backoffs",
                    "max_page_seconds",
                    "cost_recovery",
                )
                if key in result
            },
            sort_keys=True,
        ),
    }


def main() -> int:
    args = build_parser().parse_args()
    if not args.s3_bucket:
        raise RuntimeError("An S3 artifact bucket is required.")
    args.artifact_root.mkdir(parents=True, exist_ok=True)
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    free_gb = shutil.disk_usage(args.artifact_root.parent).free / (1024**3)
    claim = client.call(
        "southern.parts.automation.run",
        "claim_queued_run",
        job_types=list(SUPPORTED_JOBS),
        worker_id=args.worker_id,
        free_gb=free_gb,
        lease_seconds=900,
    )
    if not claim.get("claimed"):
        print(json.dumps({"claimed": False}, sort_keys=True))
        return 0
    if claim.get("job_type") == "sparex_discovery":
        request = dict(claim.get("request") or {})
        base_key = os.environ.get("SPAREX_DISCOVERY_RUN_KEY", "sparex-full-catalog-inventory-v3")
        request["run_key"] = resolve_discovery_run_key(client, base_key, int(claim["run_id"]))
        claim = {**claim, "request": request}
    command = build_job_command(
        claim,
        python=sys.executable,
        odoo_env_file=args.odoo_env_file,
        dealer_env_file=args.dealer_env_file,
        artifact_root=args.artifact_root / str(claim["run_id"]),
        worker_id=args.worker_id,
        s3_bucket=args.s3_bucket,
    )
    try:
        completed = subprocess.run(
            command,
            check=False,
            text=True,
            capture_output=True,
            timeout=660,
        )
        result = _last_json(completed.stdout)
        if completed.returncode:
            cooldown = warning_cooldown_minutes(completed.stderr + completed.stdout)
            client.call(
                "southern.parts.automation.run",
                "finish_claimed_run",
                run_id=claim["run_id"],
                worker_id=args.worker_id,
                state="blocked" if cooldown else "failed",
                values={
                    "error_count": 1,
                    "error_message": f"Product worker exited with status {completed.returncode}.",
                    "cooldown_minutes": cooldown,
                },
            )
            sys.stderr.write(completed.stderr)
            return completed.returncode
        result_cooldown = result_cooldown_minutes(result)
        if result_cooldown:
            client.call(
                "southern.parts.automation.run",
                "finish_claimed_run",
                run_id=claim["run_id"],
                worker_id=args.worker_id,
                state="blocked",
                values={
                    "error_count": 1,
                    "error_message": "Product worker reported a portal safety warning.",
                    "cooldown_minutes": result_cooldown,
                },
            )
            return 1
        values = finish_values(result)
        client.call(
            "southern.parts.automation.run",
            "finish_claimed_run",
            run_id=claim["run_id"],
            worker_id=args.worker_id,
            state="succeeded",
            values=values,
        )
        print(json.dumps({"claimed": True, "run_id": claim["run_id"], "result": result}, sort_keys=True))
        return 0
    except Exception as error:
        cooldown = warning_cooldown_minutes(type(error).__name__ + " " + str(error))
        client.call(
            "southern.parts.automation.run",
            "finish_claimed_run",
            run_id=claim["run_id"],
            worker_id=args.worker_id,
            state="blocked" if cooldown else "failed",
            values={
                "error_count": 1,
                "error_message": type(error).__name__,
                "cooldown_minutes": cooldown,
            },
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
