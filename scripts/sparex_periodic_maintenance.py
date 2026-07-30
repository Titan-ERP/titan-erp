from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from odoo_runtime import ApplyGate

ROOT = Path(__file__).resolve().parents[1]
PYTHON = Path(sys.executable)
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "run_reports"


def run_command(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    output = "\n".join(part for part in [proc.stdout.strip(), proc.stderr.strip()] if part)
    return proc.returncode, output


def main() -> None:
    parser = argparse.ArgumentParser(description="Run non-browser Sparex periodic maintenance tasks.")
    parser.add_argument("--image-limit", type=int, default=2000)
    parser.add_argument("--skip-images", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Run mutating maintenance steps. Default is audit-only.")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=2000)
    args = parser.parse_args()
    if args.apply:
        ApplyGate("sparex-periodic-maintenance", True, args.confirm, args.reason, args.max_records).authorize(
            args.image_limit
        )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = REPORT_DIR / f"sparex_periodic_maintenance_{stamp}.md"

    steps: list[tuple[str, list[str]]] = [
        ("Odoo connection", [str(PYTHON), str(ROOT / "scripts" / "odoo_connection_test.py")]),
    ]
    if args.apply and not args.skip_images:
        steps.append(
            (
                f"Sparex image backfill, limit {args.image_limit}",
                [str(PYTHON), str(ROOT / "scripts" / "odoo_backfill_sparex_images.py"), "--limit", str(args.image_limit)],
            )
        )
    if args.apply:
        steps.extend(
            [
                (
                    "Consolidate canonical website taxonomy/categories",
                    [str(PYTHON), str(ROOT / "scripts" / "odoo_consolidate_website_taxonomy.py"), "--apply"],
                ),
                (
                    "Enforce delivered-quantity invoicing and MTO+Buy routes",
                    [
                        str(PYTHON),
                        str(ROOT / "scripts" / "odoo_enforce_product_procurement_policy.py"),
                        "--apply",
                        "--mto-buy",
                    ],
                ),
                (
                    "Storefront publication readiness audit",
                    [
                        str(PYTHON),
                        str(ROOT / "scripts" / "odoo_cleanup_published_placeholders.py"),
                        "--apply",
                    ],
                ),
            ]
        )
    steps.append(("Odoo product audit", [str(PYTHON), str(ROOT / "scripts" / "odoo_product_live_inefficiency_audit.py")]))

    lines = [
        "# Sparex Periodic Maintenance Report",
        "",
        f"- Timestamp: {datetime.now().isoformat(timespec='seconds')}",
        f"- Workspace: {ROOT}",
        "",
    ]
    failed = False
    for title, command in steps:
        code, output = run_command(command)
        if code:
            failed = True
        lines.extend(
            [
                f"## {title}",
                "",
                f"- Exit code: {code}",
                "",
                "```text",
                output,
                "```",
                "",
            ]
        )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report: {report_path}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
