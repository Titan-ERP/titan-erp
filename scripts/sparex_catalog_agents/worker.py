"""Run bounded OpenAI catalog-agent tasks owned by Odoo.

The worker is read-only by default. Recording results requires the shared Odoo
write gate and changes only ``southern.catalog.agent.task`` records.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
from pathlib import Path

from scripts.odoo_runtime import ApplyGate, OdooClient, OdooConfig
from scripts.odoo_runtime.client import load_env_file

from .agent import AGENT_NAMES, AgentCode, run_agent

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OPENAI_ENV = ROOT / ".env.local"
DEFAULT_ODOO_ENV = ROOT / "odoo_connection.env"
WORKFLOW = "catalog-agent-results"
MAX_BATCH = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openai-env-file", type=Path, default=DEFAULT_OPENAI_ENV)
    parser.add_argument("--odoo-env-file", type=Path, default=DEFAULT_ODOO_ENV)
    parser.add_argument("--agent", choices=tuple(AGENT_NAMES), required=True)
    parser.add_argument("--limit", type=int, default=MAX_BATCH)
    parser.add_argument("--run-ai", action="store_true", help="Call the OpenAI API for the selected tasks.")
    parser.add_argument("--apply", action="store_true", help="Record agent results back into Odoo.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--worker-id", default=socket.gethostname())
    return parser


def read_tasks(client: OdooClient, agent_code: AgentCode, limit: int) -> list[dict]:
    return client.call(
        "southern.catalog.agent.task",
        "search_read",
        domain=[("agent_code", "=", agent_code), ("state", "=", "queued")],
        fields=["id", "agent_code", "external_sku", "input_json", "idempotency_key"],
        limit=limit,
        order="priority desc,create_date,id",
    )


def canonical_result(decision) -> str:
    return json.dumps(decision.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def main() -> int:
    args = build_parser().parse_args()
    if args.apply and not args.run_ai:
        raise RuntimeError("--apply requires --run-ai so claimed tasks always reach a terminal result.")
    limit = max(1, min(int(args.limit), MAX_BATCH))
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    if not client.count("ir.model", [("model", "=", "southern.catalog.agent.task")]):
        raise RuntimeError("Upgrade Southern Parts Intelligence before running catalog agents.")

    if args.apply:
        gate = ApplyGate(WORKFLOW, True, args.confirm, args.reason, MAX_BATCH)
        gate.authorize(limit)
        tasks = client.call(
            "southern.catalog.agent.task",
            "claim_tasks",
            agent_code=args.agent,
            worker_id=args.worker_id,
            limit=limit,
        )
    else:
        tasks = read_tasks(client, args.agent, limit)

    safe_plan = {
        "mode": "apply" if args.apply else "read_only",
        "agent": args.agent,
        "task_count": len(tasks),
        "task_ids": [task["id"] for task in tasks],
        "run_ai": bool(args.run_ai),
    }
    if not args.run_ai:
        print(json.dumps(safe_plan, sort_keys=True))
        return 0

    load_env_file(args.openai_env_file)
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise RuntimeError("OPENAI_API_KEY is required for --run-ai.")

    completed = 0
    failed = 0
    for task in tasks:
        try:
            snapshot = json.loads(task.get("input_json") or "{}")
            decision = run_agent(args.agent, snapshot)
            output = canonical_result(decision)
            state = "completed"
            completed += 1
        except Exception as exc:  # noqa: BLE001 - sanitized before persistence
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
        if args.apply:
            result_sha = hashlib.sha256(output.encode("utf-8")).hexdigest()
            client.call(
                "southern.catalog.agent.task",
                "record_external_result",
                task_id=task["id"],
                output_json=output,
                result_sha256=result_sha,
                state=state,
            )

    print(json.dumps({**safe_plan, "completed": completed, "failed": failed}, sort_keys=True))
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
