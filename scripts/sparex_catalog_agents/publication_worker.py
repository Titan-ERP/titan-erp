"""Publish evidence-complete Sparex products without accessing the vendor portal."""

from __future__ import annotations

import argparse
import hashlib
import json
import socket
from pathlib import Path

from scripts.odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig

from .orchestrator import (
    AGENT_SEQUENCE,
    MAX_BATCH,
    PUBLICATION_CONFIRMATION,
    _archive,
    _run_agent_tasks,
    require_company_context,
    run_s3_prefix,
    utc_stamp,
    verify_public_pages,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ODOO_ENV = ROOT / "odoo_connection.env"
DEFAULT_ARTIFACT_ROOT = ROOT / "output" / "catalog-agent-publication"
WORKFLOW = "catalog-agent-publication-only"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, default=DEFAULT_ODOO_ENV)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument(
        "--s3-prefix",
        default="sparex-product-catalog/website-publication/production",
    )
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    limit = max(1, min(int(args.limit), MAX_BATCH))
    if args.publish and not args.apply:
        raise RuntimeError("--publish requires --apply.")
    if args.apply and not args.publish:
        raise RuntimeError("Publication-only apply mode requires --publish.")

    config = OdooConfig.from_env(args.odoo_env_file)
    require_company_context(config)
    client = OdooClient(config).connect()
    preview = client.call("southern.catalog.agent.task", "preview_ready_candidates", limit=limit)
    if not args.apply:
        print(
            json.dumps(
                {
                    "mode": "read_only",
                    "candidate_count": len(preview),
                    "candidates": preview,
                    "portal_requests": 0,
                },
                sort_keys=True,
            )
        )
        return 0

    gate = ApplyGate(WORKFLOW, True, args.confirm, args.reason, MAX_BATCH)
    gate.authorize(limit)
    if not preview:
        print(
            json.dumps(
                {
                    "mode": "apply",
                    "state": "idle",
                    "candidate_count": 0,
                    "seeded_count": 0,
                    "prepared_count": 0,
                    "published_count": 0,
                    "portal_requests": 0,
                    "terminal_state": "succeeded",
                    "error": None,
                },
                sort_keys=True,
            )
        )
        return 0

    run_stamp = utc_stamp()
    store = ArtifactStore(args.artifact_root / run_stamp, schema_version="1.1")
    archive_prefix = run_s3_prefix(args.s3_prefix, run_stamp)
    seeded = client.call(
        "southern.catalog.agent.task",
        "seed_ready_candidates",
        worker_id=args.worker_id,
        limit=limit,
    )
    stages = []
    throttle_state: dict[str, float] = {}
    ai_state = {"calls": 0, "failures": 0, "disabled": False, "max_calls": 0}
    for agent_code in AGENT_SEQUENCE:
        stages.append(
            _run_agent_tasks(
                client,
                agent_code,
                worker_id=args.worker_id,
                limit=limit,
                throttle_seconds=3.0,
                throttle_state=throttle_state,
                run_ai=False,
                ai_state=ai_state,
            )
        )
    prepared = client.call(
        "southern.catalog.agent.task",
        "prepare_publication_plan",
        worker_id=args.worker_id,
        limit=limit,
    )
    plan_record = _archive(
        store,
        "publication-plan.json",
        {
            "schema_version": "1.1",
            "workflow": WORKFLOW,
            "run_stamp": run_stamp,
            "worker_id": args.worker_id,
            "reason": args.reason,
            "seeded": seeded,
            "stages": stages,
            "records": prepared,
        },
        args.s3_bucket,
        archive_prefix,
    )

    published = []
    verification = []
    error = ""
    try:
        if prepared:
            published = client.call(
                "southern.catalog.agent.task",
                "publish_prepared_tasks",
                records=prepared,
                worker_id=args.worker_id,
                confirmation=PUBLICATION_CONFIRMATION,
                reason=args.reason,
            )
            verification = verify_public_pages(config.url, published)
            verification_sha = hashlib.sha256(
                json.dumps(verification, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            client.call(
                "southern.catalog.agent.task",
                "confirm_publications",
                task_ids=[row["task_id"] for row in published],
                verification_sha256=verification_sha,
            )
    except Exception as exc:  # noqa: BLE001 - every failed public verification must roll back
        error = f"{type(exc).__name__}: {' '.join(str(exc).split())[:300]}"
        if published:
            client.call(
                "southern.catalog.agent.task",
                "rollback_publications",
                task_ids=[row["task_id"] for row in published],
                reason=error,
            )
        elif prepared:
            client.call(
                "southern.catalog.agent.task",
                "reset_prepared_publications",
                task_ids=[row["task_id"] for row in prepared],
                reason=error,
            )

    result = {
        "schema_version": "1.1",
        "workflow": WORKFLOW,
        "run_stamp": run_stamp,
        "plan_sha256": plan_record["sha256"],
        "plan_uri": plan_record["artifact_uri"],
        "candidate_count": len(preview),
        "seeded_count": len(seeded),
        "prepared_count": len(prepared),
        "published_count": len(verification) if not error else 0,
        "published": verification if not error else [],
        "portal_requests": 0,
        "error": error or None,
        "terminal_state": "failed" if error else "succeeded",
    }
    result_record = _archive(store, "result.json", result, args.s3_bucket, archive_prefix)
    result["result_sha256"] = result_record["sha256"]
    result["result_uri"] = result_record["artifact_uri"]
    print(json.dumps(result, sort_keys=True))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
