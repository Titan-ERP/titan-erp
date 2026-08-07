from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from odoo_runtime import OdooClient, OdooConfig

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
COMPANY_NAME = "Southern Equipment Company (Laurel)"


def today_utc():
    return datetime.now(UTC).date().isoformat()


def find_company(client: OdooClient) -> int:
    rows = client.execute(
        "res.company",
        "search_read",
        [[("name", "=", COMPANY_NAME)]],
        {"fields": ["id", "name"], "limit": 1},
    )
    if not rows:
        raise SystemExit(f"Could not find company: {COMPANY_NAME}")
    return int(rows[0]["id"])


def find_policy(client: OdooClient, company_id: int):
    rows = client.execute(
        "southern.accounting.automation.policy",
        "search_read",
        [[("company_id", "=", company_id), ("lane", "=", "bank_coding"), ("state", "=", "active")]],
        {"fields": ["id", "mode", "policy_version", "emergency_stop"], "order": "policy_version desc,id desc", "limit": 1},
    )
    return rows[0] if rows else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="AWS-safe launcher for Southern Accounting Automation bank coding."
    )
    parser.add_argument("--date-from", default=today_utc())
    parser.add_argument("--date-to", default=today_utc())
    parser.add_argument(
        "--mode",
        choices=["observe", "candidate", "guarded_apply"],
        help="Override the active Odoo policy mode for this run. Odoo policy still gates every write.",
    )
    parser.add_argument("--external-run-id", default="")
    parser.add_argument("--command-id", default="")
    args = parser.parse_args()

    client = OdooClient(OdooConfig.from_env(ENV_PATH)).connect()
    company_id = find_company(client)
    policy = find_policy(client, company_id)
    mode = args.mode or (policy["mode"] if policy else "observe")

    run_id = client.execute(
        "southern.bank.coding.run",
        "create",
        [
            {
                "name": f"AWS Bank Coding Automation - {args.date_from} to {args.date_to}",
                "company_id": company_id,
                "date_from": args.date_from,
                "date_to": args.date_to,
                "mode": mode,
                "worker": "aws",
                "policy_id": policy["id"] if policy else False,
                "policy_version": policy["policy_version"] if policy else 0,
            }
        ],
    )
    client.execute("southern.bank.coding.run", "action_evaluate", [[run_id]])
    rows = client.execute(
        "southern.bank.coding.run",
        "read",
        [[run_id]],
        {
            "fields": [
                "state",
                "lines_scanned",
                "candidate_count",
                "auto_applied_count",
                "finding_count",
                "unmatched_count",
                "automation_run_id",
            ]
        },
    )
    automation_run = rows[0].get("automation_run_id")
    if automation_run:
        client.execute(
            "southern.accounting.automation.run",
            "write",
            [
                [automation_run[0]],
                {
                    "external_run_id": args.external_run_id,
                    "command_id": args.command_id,
                },
            ],
        )
    print(rows[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
