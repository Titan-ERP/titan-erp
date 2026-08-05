import importlib.util
import json
import sys
import unittest
from pathlib import Path

from scripts.odoo_product_dispatch_worker import (
    build_job_command,
    finish_values,
    resolve_discovery_run_key,
    result_cooldown_minutes,
    warning_cooldown_minutes,
)


class OdooProductDispatchWorkerTests(unittest.TestCase):
    def command(self, claim):
        return build_job_command(
            claim,
            python=sys.executable,
            odoo_env_file=Path("odoo.env"),
            dealer_env_file=Path("dealer.env"),
            artifact_root=Path("artifacts"),
            worker_id="test-worker",
            s3_bucket="test-bucket",
        )

    def test_discovery_command_is_bounded_and_has_no_retry_option(self):
        command = self.command(
            {
                "job_type": "sparex_discovery",
                "mode": "evidence_only",
                "request": {
                    "job_type": "sparex_discovery",
                    "limit": 500,
                    "throttle_seconds": 1,
                    "http_retries": 0,
                },
            }
        )
        self.assertIn("scripts.sparex_catalog_discovery", command)
        self.assertEqual(command[command.index("--max-pages-per-checkpoint") + 1], "5")
        self.assertEqual(command[command.index("--throttle-seconds") + 1], "3.0")
        self.assertNotIn("--http-retries", command)

    def test_nonzero_retry_contract_is_rejected(self):
        with self.assertRaises(RuntimeError):
            self.command(
                {
                    "job_type": "sparex_discovery",
                    "request": {"job_type": "sparex_discovery", "http_retries": 1},
                }
            )

    def test_discovery_command_uses_resolved_cycle_key(self):
        command = self.command(
            {
                "job_type": "sparex_discovery",
                "request": {
                    "job_type": "sparex_discovery",
                    "run_key": "sparex-full-catalog-inventory-v3-cycle-25",
                },
            }
        )
        self.assertEqual(
            command[command.index("--run-key") + 1],
            "sparex-full-catalog-inventory-v3-cycle-25",
        )

    def test_discovery_creation_flag_requires_odoo_request(self):
        ordinary = self.command(
            {
                "job_type": "sparex_discovery",
                "request": {"job_type": "sparex_discovery", "create_missing_products": False},
            }
        )
        enabled = self.command(
            {
                "job_type": "sparex_discovery",
                "request": {"job_type": "sparex_discovery", "create_missing_products": True},
            }
        )
        self.assertNotIn("--create-missing-products", ordinary)
        self.assertIn("--create-missing-products", enabled)

    def test_discovery_cycle_key_continues_active_and_rotates_terminal_run(self):
        class Client:
            def __init__(self, rows):
                self.rows = rows

            def call(self, model, method, **params):
                self.model = model
                self.method = method
                self.params = params
                return self.rows

        base = "sparex-full-catalog-inventory-v3"
        self.assertEqual(resolve_discovery_run_key(Client([]), base, 25), base)
        self.assertEqual(
            resolve_discovery_run_key(
                Client([{"idempotency_key": f"{base}-cycle-24", "state": "running"}]),
                base,
                25,
            ),
            f"{base}-cycle-24",
        )
        for state in ("completed", "failed", "cancelled"):
            with self.subTest(state=state):
                self.assertEqual(
                    resolve_discovery_run_key(
                        Client([{"idempotency_key": base, "state": state}]),
                        base,
                        25,
                    ),
                    f"{base}-cycle-25",
                )

    def test_release_requires_apply_mode_and_publish_approval(self):
        with self.assertRaises(RuntimeError):
            self.command(
                {
                    "job_type": "catalog_release",
                    "mode": "evidence_only",
                    "request": {"job_type": "catalog_release", "publish": False},
                }
            )
        command = self.command(
            {
                "job_type": "catalog_release",
                "mode": "apply",
                "request": {
                    "job_type": "catalog_release",
                    "publish": True,
                    "http_retries": 0,
                },
            }
        )
        self.assertIn("scripts.sparex_catalog_agents.orchestrator", command)
        self.assertIn("--publish", command)

    def test_release_separates_portal_access_from_odoo_throughput(self):
        command = self.command(
            {
                "job_type": "catalog_release",
                "mode": "apply",
                "request": {
                    "job_type": "catalog_release",
                    "publish": True,
                    "limit": 500,
                    "release_limit": 500,
                    "http_retries": 0,
                },
            }
        )
        self.assertEqual(command[command.index("--cost-recovery-limit") + 1], "5")
        self.assertEqual(command[command.index("--source-repair-limit") + 1], "5")
        self.assertEqual(command[command.index("--limit") + 1], "50")

    def test_finish_values_preserve_archived_result_evidence(self):
        values = finish_values(
            {
                "prepared": 4,
                "published": 3,
                "result_uri": "s3://bucket/result.json",
                "result_sha256": "a" * 64,
            }
        )
        self.assertEqual(values["processed_count"], 4)
        self.assertEqual(values["changed_count"], 3)
        self.assertTrue(values["artifact_archived"])

    def test_finish_values_counts_discovery_corrections_and_created_drafts(self):
        values = finish_values(
            {
                "pages_processed": 5,
                "corrected": 2,
                "created_count": 3,
                "result_uri": "s3://bucket/discovery-result.json",
                "result_sha256": "c" * 64,
            }
        )
        self.assertEqual(values["changed_count"], 5)
        self.assertIn('"created_count": 3', values["evidence_summary"])

    def test_finish_values_include_cost_recovery_progress(self):
        values = finish_values(
            {
                "cost_recovery": {"state": "completed", "claimed": 5, "accepted": 4, "applied": 4},
                "result_uri": "s3://bucket/result.json",
                "result_sha256": "b" * 64,
            }
        )
        self.assertEqual(values["processed_count"], 5)
        self.assertEqual(values["changed_count"], 4)
        self.assertEqual(
            json.loads(values["evidence_summary"])["cost_recovery"]["accepted"],
            4,
        )

    def test_warning_signals_enforce_sixty_minute_cooldown(self):
        for warning in (
            "portal_http_429",
            "HTTP 503",
            "html_proxy_error",
            "dealer_login_failed",
            "TimeoutExpired",
            "slow page",
            "Odoo transient",
        ):
            with self.subTest(warning=warning):
                self.assertEqual(warning_cooldown_minutes(warning), 60)
        self.assertEqual(warning_cooldown_minutes("validation error"), 0)

    def test_success_result_ignores_warning_like_candidate_values(self):
        result = {
            "cost_recovery": {"state": "completed", "claimed": 5, "applied": 5},
            "candidates": [
                {
                    "product_id": 500,
                    "public_url": "https://example.com/shop/part-503",
                    "http_status": 200,
                }
            ],
            "published": 5,
            "failed": False,
        }
        self.assertEqual(result_cooldown_minutes(result), 0)

    def test_structured_result_warning_enforces_cooldown(self):
        self.assertEqual(
            result_cooldown_minutes({"cost_recovery": {"state": "portal_cooldown"}}),
            60,
        )
        self.assertEqual(result_cooldown_minutes({"error_code": "portal_http_503"}), 60)

    def test_systemd_worker_is_poll_only_and_installer_disables_old_timer(self):
        root = Path(__file__).resolve().parents[1]
        service = (root / "cloud" / "aws" / "titan-sparex-discovery.service").read_text()
        timer = (root / "cloud" / "aws" / "titan-sparex-discovery.timer").read_text()
        installer = (root / "cloud" / "aws" / "install-product-dispatch-worker.sh").read_text()
        self.assertNotIn("OnSuccess=titan-catalog-agent.service", service)
        self.assertIn("ExecStart=/bin/bash", service)
        self.assertIn("scripts/run_sparex_catalog_discovery.sh", service)
        self.assertIn("OnUnitInactiveSec=1min", timer)
        self.assertIn("disable --now titan-catalog-agent.timer", installer)
        self.assertIn("enable --now titan-sparex-discovery.timer", installer)

    def test_upgrade_migration_repairs_only_known_orchestration_records(self):
        root = Path(__file__).resolve().parents[1]
        migration_path = root / "southern_parts_intelligence" / "migrations" / "19.0.1.13.0" / "post-migrate.py"
        spec = importlib.util.spec_from_file_location("parts_orchestration_migration", migration_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        calls = []

        class Cursor:
            def execute(self, query, params=None):
                calls.append((query, params))

        module.migrate(Cursor(), "19.0.1.12.0")

        self.assertEqual(len(calls), 2)
        self.assertIn("southern_parts_catalog_sync_sparex_updates", calls[0][0])
        self.assertIn("mode = 'sparex_discovery'", calls[0][0])
        self.assertIn("internal_cron_enabled = FALSE", calls[0][0])
        self.assertIn("southern_parts_catalog_sync_snapshot_refresh", calls[1][0])
        self.assertEqual(calls[1][1], ["%does not match format '%Y-%m-%d %H:%M:%S'%"])


if __name__ == "__main__":
    unittest.main()
