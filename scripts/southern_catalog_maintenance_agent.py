from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run_step(name: str, command: list[str], dry_run: bool = False) -> int:
    print(f"\n== {name} ==")
    print(" ".join(command))
    if dry_run:
        return 0
    completed = subprocess.run(command, cwd=ROOT)
    if completed.returncode:
        print(f"FAILED: {name} exited with {completed.returncode}")
    return completed.returncode


def main() -> None:
    parser = argparse.ArgumentParser(description="Southern Equipment recurring catalog maintenance agent.")
    parser.add_argument("--apply", action="store_true", help="Apply Odoo writes. Without this, publish/repair scripts run in dry-run/default modes where supported.")
    parser.add_argument("--skip-pricing-refresh", action="store_true")
    parser.add_argument("--skip-taxonomy", action="store_true")
    parser.add_argument("--skip-procurement-policy", action="store_true")
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--skip-membership-fix", action="store_true")
    parser.add_argument("--skip-membership-audit", action="store_true")
    parser.add_argument("--pricing-limit", type=int, default=0, help="Limit pricing refresh rows. Default 0 checks all active source URLs.")
    parser.add_argument("--run-name", default="", help="Optional pricing refresh run name.")
    args = parser.parse_args()

    failures = 0

    if not args.skip_pricing_refresh:
        command = [PYTHON, "scripts/refresh_retail_price_sources.py", "--delay", "0.25"]
        if args.pricing_limit:
            command.extend(["--limit", str(args.pricing_limit)])
        if args.run_name:
            command.extend(["--run-name", args.run_name])
        failures += run_step("Refresh retail price source URLs", command)

    if not args.skip_taxonomy:
        command = [PYTHON, "scripts/odoo_consolidate_website_taxonomy.py"]
        if args.apply:
            command.append("--apply")
        failures += run_step("Consolidate canonical website taxonomy/categories", command)

    if not args.skip_procurement_policy:
        command = [PYTHON, "scripts/odoo_enforce_product_procurement_policy.py", "--mto-buy"]
        if args.apply:
            command.append("--apply")
        failures += run_step("Enforce delivered-quantity invoicing and MTO+Buy routes", command)

    if not args.skip_cleanup:
        command = [PYTHON, "scripts/odoo_cleanup_published_placeholders.py"]
        if args.apply:
            command.append("--apply")
        failures += run_step("Final storefront readiness cleanup", command)

    if args.apply and not args.skip_membership_fix:
        failures += run_step("Fix Standard Membership service product", [PYTHON, "scripts/odoo_fix_standard_membership_service.py"])

    if not args.skip_membership_audit:
        failures += run_step("Audit Standard Membership website product", [PYTHON, "scripts/odoo_audit_standard_membership.py"])

    if failures:
        raise SystemExit(failures)
    print("\nCatalog maintenance completed.")


if __name__ == "__main__":
    main()
