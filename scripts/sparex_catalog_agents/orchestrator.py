"""Run the bounded Odoo catalog-agent chain and verified website release.

The OpenAI agents can only interpret Odoo-owned snapshots. Product publication
is a separate deterministic Odoo transaction protected by a plan, rollback
snapshot, explicit write gate, public verification, and scoped rollback.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import socket
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from scripts.odoo_runtime import ApplyGate, ArtifactStore, OdooClient, OdooConfig
from scripts.odoo_runtime.client import load_env_file

from .agent import AGENT_NAMES, AgentCode, deterministic_agent_decision, requires_ai_review, run_agent
from .cost_recovery import PortalCooldownError, recover_dealer_costs
from .worker import canonical_result

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ODOO_ENV = ROOT / "odoo_connection.env"
DEFAULT_OPENAI_ENV = ROOT / ".env.local"
DEFAULT_ARTIFACT_ROOT = ROOT / "outputs" / "catalog-agent-automation"
WORKFLOW = "catalog-agent-automation"
PUBLICATION_CONFIRMATION = "catalog-agent-publication"
QUOTE_PUBLICATION_CONFIRMATION = "sparex-quote-only-publication"
SOURCE_LINK_CONFIRMATION = "sparex-discovery-source-link"
DESCRIPTION_REPAIR_CONFIRMATION = "sparex-listing-description-repair"
MAX_BATCH = 50
MAX_EXTERNAL_REPAIR_BATCH = 5
MAX_AI_CALLS = 5
MAX_SOURCE_IMAGE_BYTES = 10 * 1024 * 1024
DEFAULT_COST_RECOVERY_LIMIT = 5
AGENT_SEQUENCE: tuple[AgentCode, ...] = (
    "coordinator",
    "sparex_discovery",
    "odoo_match",
    "product_verification",
    "website_release",
)


def utc_stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")


def run_s3_prefix(base_prefix: str, run_stamp: str) -> str:
    return f"{base_prefix.rstrip('/')}/{run_stamp}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, default=DEFAULT_ODOO_ENV)
    parser.add_argument("--dealer-env-file", type=Path)
    parser.add_argument("--openai-env-file", type=Path, default=DEFAULT_OPENAI_ENV)
    parser.add_argument("--artifact-root", type=Path, default=DEFAULT_ARTIFACT_ROOT)
    parser.add_argument("--s3-bucket", default=os.environ.get("SOUTHERN_PRODUCT_ARTIFACT_BUCKET", ""))
    parser.add_argument(
        "--s3-prefix",
        default="sparex-product-catalog/catalog-agent-automation/production",
    )
    parser.add_argument("--limit", type=int, default=MAX_BATCH)
    parser.add_argument("--cost-recovery-limit", type=int, default=DEFAULT_COST_RECOVERY_LIMIT)
    parser.add_argument("--source-repair-limit", type=int, default=MAX_EXTERNAL_REPAIR_BATCH)
    parser.add_argument("--skip-cost-recovery", action="store_true")
    parser.add_argument("--throttle-seconds", type=float, default=3.0)
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--run-ai", action="store_true")
    parser.add_argument(
        "--ai-max-calls",
        type=int,
        default=int(os.environ.get("OPENAI_CATALOG_AGENT_MAX_CALLS", "1")),
        help="Maximum ambiguous-task model invocations for this run (hard-capped at 5).",
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    return parser


def _archive(store: ArtifactStore, name: str, payload: Any, bucket: str, prefix: str) -> dict[str, Any]:
    record_count = len(payload) if isinstance(payload, list) else 1
    record = store.write_json(name, payload, record_count=record_count)
    if not bucket:
        raise RuntimeError("SOUTHERN_PRODUCT_ARTIFACT_BUCKET or --s3-bucket is required.")
    return store.archive_s3(record, bucket=bucket, prefix=prefix)


def _run_agent_tasks(
    client: OdooClient,
    agent_code: AgentCode,
    *,
    worker_id: str,
    limit: int,
    throttle_seconds: float,
    throttle_state: dict[str, float],
    run_ai: bool,
    ai_state: dict[str, Any],
) -> dict[str, Any]:
    tasks = client.call(
        "southern.catalog.agent.task",
        "claim_tasks",
        agent_code=agent_code,
        worker_id=worker_id,
        limit=limit,
    )
    completed = 0
    failed = 0
    deterministic_decisions = 0
    ai_calls = 0
    ai_failures = 0
    task_ids: list[int] = []
    for task in tasks:
        task_ids.append(int(task["id"]))
        snapshot = json.loads(task.get("input_json") or "{}")
        use_ai = (
            run_ai
            and requires_ai_review(snapshot)
            and not ai_state["disabled"]
            and ai_state["calls"] < ai_state["max_calls"]
        )
        try:
            if use_ai:
                wait_seconds = throttle_seconds - (time.monotonic() - throttle_state.get("last_call", 0.0))
                if wait_seconds > 0:
                    time.sleep(wait_seconds)
                throttle_state["last_call"] = time.monotonic()
                ai_state["calls"] += 1
                ai_calls += 1
                output = canonical_result(run_agent(agent_code, snapshot))
            else:
                output = canonical_result(deterministic_agent_decision(agent_code, snapshot))
                deterministic_decisions += 1
            state = "completed"
            completed += 1
        except Exception as exc:  # noqa: BLE001 - never persist provider detail or secrets
            if use_ai:
                output = canonical_result(deterministic_agent_decision(agent_code, snapshot))
                state = "completed"
                completed += 1
                deterministic_decisions += 1
                ai_failures += 1
                ai_state["failures"] += 1
                ai_state["disabled"] = True
            else:
                output = json.dumps(
                    {
                        "decision": "needs_review",
                        "summary": f"Agent execution failed: {type(exc).__name__}",
                        "confidence": 0.0,
                        "blocking_reasons": ["agent_execution_failed"],
                        "next_agent": None,
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
                state = "failed"
                failed += 1
        client.call(
            "southern.catalog.agent.task",
            "record_external_result",
            task_id=task["id"],
            output_json=output,
            result_sha256=hashlib.sha256(output.encode("utf-8")).hexdigest(),
            state=state,
        )
    return {
        "agent": agent_code,
        "agent_name": AGENT_NAMES[agent_code],
        "claimed": len(tasks),
        "completed": completed,
        "failed": failed,
        "deterministic_decisions": deterministic_decisions,
        "ai_calls": ai_calls,
        "ai_failures": ai_failures,
        "task_ids": task_ids,
    }


def _public_url(base_url: str, path: str) -> str:
    if path.startswith(("http://", "https://")):
        url = path
    else:
        url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    return quote(url, safe=":/?#[]@!$&'()*+,;=%")


def verify_public_pages(base_url: str, published: list[dict[str, Any]]) -> list[dict[str, Any]]:
    verification = []
    for row in published:
        url = _public_url(base_url, row["public_path"])
        request = urllib.request.Request(url, headers={"User-Agent": "Titan-Catalog-Release-Verifier/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = int(response.status)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(
                f"Public verification failed for product {row['product_id']}: {type(exc).__name__}"
            ) from exc
        sku = str(row["sku"])
        if status != 200 or sku.casefold() not in body.casefold():
            raise RuntimeError(f"Public verification failed for product {row['product_id']}: status_or_sku")
        verification.append(
            {
                "task_id": row["task_id"],
                "product_id": row["product_id"],
                "sku": sku,
                "public_url": url,
                "http_status": status,
                "exact_sku_present": True,
            }
        )
    return verification


def hydrate_source_repair_images(records: list[dict[str, Any]], throttle_seconds: float) -> list[dict[str, Any]]:
    hydrated = []
    last_request = 0.0
    for record in records:
        prepared = dict(record)
        if prepared.get("repair_image"):
            image_url = str(prepared.get("image_url") or "").strip()
            parsed = urlsplit(image_url)
            if parsed.scheme.casefold() != "https" or not parsed.hostname:
                raise RuntimeError("source_image_url_invalid")
            expected_url_sha = str(prepared.get("image_url_sha256") or "").casefold()
            if hashlib.sha256(image_url.encode("utf-8")).hexdigest() != expected_url_sha:
                raise RuntimeError("source_image_url_hash_mismatch")
            wait_seconds = throttle_seconds - (time.monotonic() - last_request)
            if wait_seconds > 0:
                time.sleep(wait_seconds)
            request = urllib.request.Request(
                image_url,
                headers={"User-Agent": "Titan-Sparex-Listing-Image-Repair/1.0"},
            )
            try:
                with urllib.request.urlopen(request, timeout=45) as response:
                    content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].casefold()
                    content = response.read(MAX_SOURCE_IMAGE_BYTES + 1)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise RuntimeError(f"source_image_fetch_failed:{type(exc).__name__}") from exc
            finally:
                last_request = time.monotonic()
            if not content_type.startswith("image/") or not content or len(content) > MAX_SOURCE_IMAGE_BYTES:
                raise RuntimeError("source_image_content_invalid")
            prepared["image_base64"] = base64.b64encode(content).decode("ascii")
            prepared["image_content_sha256"] = hashlib.sha256(content).hexdigest()
        hydrated.append(prepared)
    return hydrated


def main() -> int:
    args = build_parser().parse_args()
    limit = max(1, min(int(args.limit), MAX_BATCH))
    source_repair_limit = max(
        1,
        min(int(args.source_repair_limit), MAX_EXTERNAL_REPAIR_BATCH),
    )
    cost_recovery_limit = max(
        1,
        min(int(args.cost_recovery_limit), MAX_EXTERNAL_REPAIR_BATCH),
    )
    throttle = max(3.0, float(args.throttle_seconds))
    if args.publish and not args.apply:
        raise RuntimeError("--publish requires --apply.")
    ai_max_calls = max(0, min(int(args.ai_max_calls), MAX_AI_CALLS))
    config = OdooConfig.from_env(args.odoo_env_file)
    client = OdooClient(config).connect()
    run_stamp = utc_stamp()
    store = ArtifactStore(args.artifact_root / run_stamp, schema_version="1.1")
    archive_prefix = run_s3_prefix(args.s3_prefix, run_stamp)
    cost_recovery: dict[str, Any] = {"state": "skipped", "claimed": 0, "accepted": 0, "applied": 0}
    if args.apply and not args.skip_cost_recovery:
        ApplyGate(WORKFLOW, True, args.confirm, args.reason, MAX_BATCH).authorize(cost_recovery_limit)
        try:
            cost_recovery = recover_dealer_costs(
                client,
                worker_id=args.worker_id,
                limit=cost_recovery_limit,
                dealer_env_file=(args.dealer_env_file or args.odoo_env_file),
                throttle_seconds=throttle,
                store=store,
                artifact_root=args.artifact_root,
                s3_bucket=args.s3_bucket,
                s3_prefix=archive_prefix,
                reason=args.reason,
            )
        except PortalCooldownError:
            print(
                json.dumps(
                    {
                        "mode": "apply",
                        "cost_recovery": {"state": "portal_cooldown", "write_blocked": True},
                        "published": 0,
                        "failed": True,
                    },
                    sort_keys=True,
                )
            )
            return 1
        if cost_recovery.get("write_blocked"):
            print(json.dumps({"mode": "apply", "cost_recovery": cost_recovery, "published": 0, "failed": True}, sort_keys=True))
            return 1
    readiness_refresh = client.call(
        "southern.sparex.discovery.item",
        "refresh_readiness_batch",
        limit=min(2000, max(500, limit * 10)),
    )
    source_prepared = client.call(
        "southern.sparex.discovery.item",
        "prepare_source_link_plan",
        limit=source_repair_limit,
    )
    description_prepared = client.call(
        "southern.sparex.discovery.item",
        "prepare_description_repair_plan",
        limit=limit,
    )
    quote_preview = client.call(
        "southern.vendor.catalog.item",
        "prepare_quote_publication_plan",
        limit=limit,
        company_id=config.company_id or False,
    )
    preview = client.call("southern.catalog.agent.task", "preview_ready_candidates", limit=limit)
    safe_preview = {
        "mode": "apply" if args.apply else "read_only",
        "source_link_candidate_count": len(source_prepared),
        "description_repair_candidate_count": len(description_prepared),
        "quote_publication_candidate_count": len(quote_preview),
        "readiness_refresh": readiness_refresh,
        "candidate_count": len(preview),
        "candidates": preview,
        "limit": limit,
        "source_repair_limit": source_repair_limit,
        "cost_recovery": cost_recovery,
    }
    if not args.apply:
        print(json.dumps(safe_preview, sort_keys=True))
        return 0

    gate = ApplyGate(WORKFLOW, True, args.confirm, args.reason, MAX_BATCH)
    gate.authorize(max(len(preview), len(source_prepared), len(description_prepared), len(quote_preview)))
    if args.run_ai and ai_max_calls:
        if args.openai_env_file.exists():
            load_env_file(args.openai_env_file)
        if not os.environ.get("OPENAI_API_KEY", "").strip():
            raise RuntimeError("OPENAI_API_KEY is required only for explicit ambiguous AI review.")

    source_plan = {
        "schema_version": "1.1",
        "workflow": WORKFLOW,
        "run_stamp": run_stamp,
        "reason": args.reason,
        "records": [
            {
                key: row.get(key)
                for key in (
                    "item_id",
                    "product_id",
                    "sku",
                    "source_url_sha256",
                    "image_url_sha256",
                    "source_artifact_sha256",
                    "before_source_url_sha256",
                    "before_image_present",
                    "before_image_sha256",
                    "repair_url",
                    "repair_image",
                    "snapshot_sha256",
                )
            }
            for row in source_prepared
        ],
    }
    source_plan_record = _archive(store, "source-link-plan.json", source_plan, args.s3_bucket, archive_prefix)
    source_rollback_record = _archive(
        store,
        "source-link-rollback.json",
        {"schema_version": "1.1", "workflow": WORKFLOW, "run_stamp": run_stamp, "records": source_prepared},
        args.s3_bucket,
        archive_prefix,
    )
    linked_sources = []
    if source_prepared:
        source_execution = hydrate_source_repair_images(source_prepared, throttle)
        linked_sources = client.call(
            "southern.sparex.discovery.item",
            "apply_source_link_plan",
            records=source_execution,
            confirmation=SOURCE_LINK_CONFIRMATION,
            reason=args.reason,
        )
    readiness_refresh = client.call(
        "southern.sparex.discovery.item",
        "refresh_readiness_batch",
        limit=min(2000, max(500, limit * 10)),
    )
    description_prepared = client.call(
        "southern.sparex.discovery.item",
        "prepare_description_repair_plan",
        limit=limit,
    )
    description_plan = {
        "schema_version": "1.1",
        "workflow": WORKFLOW,
        "run_stamp": run_stamp,
        "reason": args.reason,
        "records": description_prepared,
    }
    description_plan_record = _archive(
        store, "description-repair-plan.json", description_plan, args.s3_bucket, archive_prefix
    )
    description_rollback_record = _archive(
        store,
        "description-repair-rollback.json",
        {"schema_version": "1.1", "workflow": WORKFLOW, "run_stamp": run_stamp, "records": description_prepared},
        args.s3_bucket,
        archive_prefix,
    )
    repaired_descriptions = []
    if description_prepared:
        repaired_descriptions = client.call(
            "southern.sparex.discovery.item",
            "apply_description_repair_plan",
            records=description_prepared,
            confirmation=DESCRIPTION_REPAIR_CONFIRMATION,
            reason=args.reason,
        )
    preview = client.call("southern.catalog.agent.task", "preview_ready_candidates", limit=limit)
    safe_preview.update(
        {
            "source_linked_count": len(linked_sources),
            "description_repaired_count": len(repaired_descriptions),
            "description_repair_candidate_count": len(description_prepared),
            "readiness_refresh": readiness_refresh,
            "candidate_count": len(preview),
            "candidates": preview,
        }
    )
    seeded = client.call(
        "southern.catalog.agent.task",
        "seed_ready_candidates",
        worker_id=args.worker_id,
        limit=limit,
    )
    plan = {
        "schema_version": "1.1",
        "workflow": WORKFLOW,
        "run_stamp": run_stamp,
        "worker_id": args.worker_id,
        "reason": args.reason,
        "source_link_plan_sha256": source_plan_record["sha256"],
        "source_link_rollback_sha256": source_rollback_record["sha256"],
        "source_linked_count": len(linked_sources),
        "description_repair_plan_sha256": description_plan_record["sha256"],
        "description_repair_rollback_sha256": description_rollback_record["sha256"],
        "description_repaired_count": len(repaired_descriptions),
        "seeded": seeded,
        "idempotency_key": gate.idempotency_key(seeded),
    }
    plan_record = _archive(store, "plan.json", plan, args.s3_bucket, archive_prefix)

    stages = []
    throttle_state: dict[str, float] = {}
    ai_state: dict[str, Any] = {
        "calls": 0,
        "failures": 0,
        "disabled": False,
        "max_calls": ai_max_calls,
    }
    for agent_code in AGENT_SEQUENCE:
        stages.append(
            _run_agent_tasks(
                client,
                agent_code,
                worker_id=args.worker_id,
                limit=limit,
                throttle_seconds=throttle,
                throttle_state=throttle_state,
                run_ai=bool(args.run_ai),
                ai_state=ai_state,
            )
        )

    prepared = client.call(
        "southern.catalog.agent.task",
        "prepare_publication_plan",
        worker_id=args.worker_id,
        limit=limit,
    )
    quote_prepared = client.call(
        "southern.vendor.catalog.item",
        "prepare_quote_publication_plan",
        limit=limit,
        company_id=config.company_id or False,
    )
    rollback_payload = {
        "schema_version": "1.1",
        "workflow": WORKFLOW,
        "run_stamp": run_stamp,
        "records": prepared,
    }
    rollback_record = _archive(store, "rollback.json", rollback_payload, args.s3_bucket, archive_prefix)
    quote_plan_record = _archive(
        store,
        "quote-only-plan.json",
        {
            "schema_version": "1.1",
            "workflow": WORKFLOW,
            "run_stamp": run_stamp,
            "records": quote_prepared,
        },
        args.s3_bucket,
        archive_prefix,
    )
    published: list[dict[str, Any]] = []
    verification: list[dict[str, Any]] = []
    quote_published: list[dict[str, Any]] = []
    quote_verification: list[dict[str, Any]] = []
    error = ""
    try:
        if args.publish and prepared:
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
        if args.publish and quote_prepared:
            quote_published = client.call(
                "southern.vendor.catalog.item",
                "apply_quote_publication_plan",
                records=quote_prepared,
                artifact_uri=quote_plan_record["artifact_uri"],
                artifact_sha256=quote_plan_record["sha256"],
                confirmation=QUOTE_PUBLICATION_CONFIRMATION,
                reason=args.reason,
            )
            quote_verification = verify_public_pages(config.url, quote_published)
    except Exception as exc:  # noqa: BLE001 - rollback must run for every verification failure
        error = f"{type(exc).__name__}: publication_or_verification_failed"
        if quote_published:
            client.call(
                "southern.vendor.catalog.item",
                "rollback_quote_publications",
                records=quote_published,
                reason=error,
            )
        if published:
            client.call(
                "southern.catalog.agent.task",
                "rollback_publications",
                task_ids=[row["task_id"] for row in published],
                reason=error,
            )
        if repaired_descriptions:
            client.call(
                "southern.sparex.discovery.item",
                "rollback_description_repairs",
                records=repaired_descriptions,
                reason=error,
            )
        if linked_sources:
            client.call(
                "southern.sparex.discovery.item",
                "rollback_source_links",
                records=linked_sources,
                reason=error,
            )

    result = {
        "schema_version": "1.1",
        "workflow": WORKFLOW,
        "run_stamp": run_stamp,
        "plan_sha256": plan_record["sha256"],
        "plan_uri": plan_record["artifact_uri"],
        "source_link_plan_sha256": source_plan_record["sha256"],
        "source_link_plan_uri": source_plan_record["artifact_uri"],
        "source_link_rollback_sha256": source_rollback_record["sha256"],
        "source_link_rollback_uri": source_rollback_record["artifact_uri"],
        "source_linked_count": 0 if error else len(linked_sources),
        "description_repair_plan_sha256": description_plan_record["sha256"],
        "description_repair_plan_uri": description_plan_record["artifact_uri"],
        "description_repair_rollback_sha256": description_rollback_record["sha256"],
        "description_repair_rollback_uri": description_rollback_record["artifact_uri"],
        "description_repaired_count": 0 if error else len(repaired_descriptions),
        "rollback_sha256": rollback_record["sha256"],
        "rollback_uri": rollback_record["artifact_uri"],
        "quote_plan_sha256": quote_plan_record["sha256"],
        "quote_plan_uri": quote_plan_record["artifact_uri"],
        "stages": stages,
        "ai": {
            "enabled": bool(args.run_ai),
            "max_calls": ai_max_calls,
            "calls": ai_state["calls"],
            "failures": ai_state["failures"],
            "disabled_after_failure": ai_state["disabled"],
        },
        "prepared_count": len(prepared),
        "quote_prepared_count": len(quote_prepared),
        "published_count": (len(verification) + len(quote_verification)) if not error else 0,
        "standard_published_count": len(verification) if not error else 0,
        "quote_published_count": len(quote_verification) if not error else 0,
        "published": verification if not error else [],
        "quote_published": quote_verification if not error else [],
        "error": error or None,
        "terminal_state": "failed" if error else "succeeded",
    }
    result_record = _archive(store, "result.json", result, args.s3_bucket, archive_prefix)
    summary = {
        **safe_preview,
        "seeded": len(seeded),
        "source_linked": 0 if error else len(linked_sources),
        "description_repaired": 0 if error else len(repaired_descriptions),
        "prepared": len(prepared),
        "quote_prepared": len(quote_prepared),
        "published": (len(verification) + len(quote_verification)) if not error else 0,
        "standard_published": len(verification) if not error else 0,
        "quote_published": len(quote_verification) if not error else 0,
        "failed": bool(error),
        "ai_calls": ai_state["calls"],
        "ai_failures": ai_state["failures"],
        "plan_sha256": plan_record["sha256"],
        "rollback_sha256": rollback_record["sha256"],
        "result_sha256": result_record["sha256"],
        "result_uri": result_record["artifact_uri"],
    }
    print(json.dumps(summary, sort_keys=True))
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
