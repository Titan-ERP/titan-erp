from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from odoo_runtime import ApplyGate

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
DEFAULT_ENV_FILES = [ROOT / "cloud" / "aws" / ".env", ROOT / "odoo_connection.env"]


def load_env_files(paths: list[Path]) -> None:
    for path in paths:
        if not path.exists():
            continue
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bounded Sparex/Odoo worker for EC2 or another cloud VM.")
    parser.add_argument("--env-file", action="append", default=[], help="Environment file to load before running.")
    parser.add_argument("--min-free-gb", type=float, default=float(os.environ.get("SOUTHERN_MIN_FREE_GB", "2")))
    parser.add_argument("--order-limit", type=int, default=10)
    parser.add_argument("--dealer-limit", type=int, default=10)
    parser.add_argument(
        "--sku",
        action="append",
        default=[],
        help="Target one existing Sparex SKU. Can be passed multiple times. Dealer sync only.",
    )
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--max-errors", type=int, default=3)
    parser.add_argument("--skip-order-refresh", action="store_true")
    parser.add_argument("--skip-dealer-sync", action="store_true")
    parser.add_argument("--archive", action="store_true", help="Upload evidence/reports to S3 before and after work.")
    parser.add_argument("--archive-since-hours", type=float, default=6)
    parser.add_argument(
        "--archive-delete-local",
        action="store_true",
        help="After verified S3 upload, delete uploaded local artifacts older than --archive-retain-local-hours.",
    )
    parser.add_argument("--archive-retain-local-hours", type=float, default=24)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Allow child Odoo mutation flags. Default is evidence-only.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=20)
    return parser.parse_args()


def free_gb(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free / (1024**3)


def run_step(name: str, command: list[str], dry_run: bool) -> int:
    print(f"== {name} ==")
    print(" ".join(command))
    if dry_run:
        return 0
    completed = subprocess.run(command, cwd=ROOT, text=True)
    print(f"{name}_exit={completed.returncode}")
    return completed.returncode


def main() -> int:
    args = parse_args()
    env_files = [Path(p).resolve() for p in args.env_file] if args.env_file else DEFAULT_ENV_FILES
    load_env_files(env_files)
    available = free_gb(ROOT)
    print(f"free_gb={available:.2f} min_free_gb={args.min_free_gb:.2f}")
    if available < args.min_free_gb:
        print("SKIP: below disk safety floor")
        return 75
    if args.apply:
        ApplyGate("aws-sparex-worker", True, args.confirm, args.reason, args.max_records).authorize(
            args.order_limit + args.dealer_limit
        )

    exit_code = 0
    archive_command = [
        PYTHON,
        "scripts/aws_archive_product_artifacts.py",
        "--since-hours",
        str(args.archive_since_hours),
        "--retain-local-hours",
        str(args.archive_retain_local_hours),
    ]
    if args.archive_delete_local:
        archive_command.append("--delete-local-after-upload")

    if args.archive:
        code = run_step("s3_archive_preflight", archive_command, args.dry_run)
        exit_code = exit_code or code

    if not args.skip_order_refresh:
        order_command = [
            PYTHON,
            "scripts/odoo_refresh_recent_order_sparex_products.py",
            "--since-minutes",
            "120",
            "--limit",
            str(args.order_limit),
            "--delay",
            str(args.delay),
        ]
        if args.apply:
            order_command.extend(
                [
                    "--apply",
                    "--confirm",
                    "recent-order-sparex-refresh",
                    "--reason",
                    args.reason,
                    "--max-records",
                    str(args.max_records),
                ]
            )
        code = run_step(
            "order_refresh",
            order_command,
            args.dry_run,
        )
        exit_code = exit_code or code

    if not args.skip_dealer_sync and exit_code == 0:
        dealer_command = [
            PYTHON,
            "scripts/sparex_dealer_portal_sync.py",
            "--limit",
            str(args.dealer_limit),
            "--delay",
            str(args.delay),
            "--max-errors",
            str(args.max_errors),
        ]
        if args.apply:
            dealer_command.extend(["--apply-cost", "--apply-source-url", "--apply-supplierinfo"])
        for sku in args.sku:
            dealer_command.extend(["--sku", sku])
        code = run_step(
            "dealer_sync",
            dealer_command,
            args.dry_run,
        )
        exit_code = exit_code or code

    if args.archive and exit_code == 0:
        code = run_step("s3_archive_after_work", archive_command, args.dry_run)
        exit_code = exit_code or code

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
