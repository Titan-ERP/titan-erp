import hashlib

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("at_install", "-post_install")
class TestAutomationControl(TransactionCase):
    def setUp(self):
        super().setUp()
        self.sync = self.env["southern.parts.catalog.sync"].create({"name": "Test controlled catalog workflow"})

    def test_safe_defaults(self):
        self.assertEqual(self.sync.minimum_free_gb, 2.0)
        self.assertFalse(self.sync.internal_cron_enabled)

    def test_external_run_requires_idempotency_key(self):
        with self.assertRaises(UserError):
            self.env["southern.parts.automation.run"].begin_external_run(
                self.sync.id,
                {
                    "mode": "dry_run",
                    "free_gb": 3.0,
                },
            )

    def test_internal_run_is_disabled_until_reviewed(self):
        with self.assertRaises(UserError):
            self.env["southern.parts.automation.run"].begin_internal_run(
                self.sync.id,
                {
                    "mode": "maintenance",
                    "idempotency_key": "test-internal-disabled",
                },
            )

    def test_odoo_queues_and_aws_claims_sparex_evidence(self):
        self.sync.write(
            {
                "mode": "sparex_discovery",
                "batch_size": 5,
                "internal_cron_enabled": True,
            }
        )
        run_id = self.sync._run_one_batch()
        run = self.env["southern.parts.automation.run"].browse(run_id)
        self.assertEqual(run.state, "queued")
        self.assertEqual(run.job_type, "sparex_discovery")
        self.assertEqual(run.mode, "evidence_only")
        claim = self.env["southern.parts.automation.run"].claim_queued_run(
            ["sparex_discovery"], "aws-test-worker", 3.0, 900
        )
        self.assertTrue(claim["claimed"])
        self.assertEqual(claim["run_id"], run.id)
        self.assertEqual(claim["request"]["limit"], 5)
        self.env["southern.parts.automation.run"].finish_claimed_run(
            run.id,
            "aws-test-worker",
            "succeeded",
            {"processed_count": 5, "http_request_count": 5},
        )
        self.assertEqual(run.state, "succeeded")
        self.assertTrue(self.sync.next_allowed_run_at)

    def test_release_dispatch_requires_odoo_approval(self):
        self.sync.write({"mode": "sparex_discovery"})
        with self.assertRaises(UserError):
            self.sync.action_queue_approved_apply()
        self.sync.action_request_approval()
        self.sync.action_approve()
        run_id = (
            self.sync.action_queue_approved_apply()
            and self.sync.run_ids.filtered(lambda row: row.job_type == "catalog_release").id
        )
        run = self.env["southern.parts.automation.run"].browse(run_id)
        self.assertEqual(run.state, "queued")
        self.assertEqual(run.mode, "apply")
        self.assertFalse(self.sync.internal_cron_enabled)
        self.assertEqual(self.sync.state, "paused")
        self.assertEqual(self.sync.approval_state, "approved")

    def test_dispatch_schedule_requires_approval_and_consumes_it(self):
        self.sync.write({"mode": "sparex_discovery"})
        with self.assertRaises(UserError):
            self.sync.action_enable_dispatch_schedule()
        self.sync.action_request_approval()
        self.sync.action_approve()
        self.sync.action_enable_dispatch_schedule()
        self.assertTrue(self.sync.internal_cron_enabled)
        self.assertEqual(self.sync.approval_state, "not_required")

    def test_continuous_release_keeps_approval_and_queues_discovery_first(self):
        self.sync.write({"mode": "sparex_discovery", "batch_size": 5})
        self.sync.action_request_approval()
        self.sync.action_approve()
        self.sync.action_enable_continuous_release()

        cron = self.env.ref("southern_parts_intelligence.ir_cron_southern_parts_catalog_sync")
        run_id = self.sync._run_one_batch()
        run = self.env["southern.parts.automation.run"].browse(run_id)

        self.assertTrue(self.sync.continuous_release_enabled)
        self.assertTrue(self.sync.internal_cron_enabled)
        self.assertEqual(self.sync.approval_state, "approved")
        self.assertTrue(cron.active)
        self.assertEqual(cron.interval_number, 1)
        self.assertEqual(cron.interval_type, "minutes")
        self.assertEqual(run.job_type, "sparex_discovery")
        self.assertEqual(run.requested_count, 5)

    def test_continuous_release_queues_apply_after_discovery_and_preserves_approval(self):
        product = self.env["product.template"].create(
            {"name": "Continuous release part", "default_code": "S.700001", "list_price": 25}
        )
        seed_url = "https://us.sparex.com/products?p=1"
        discovery = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "continuous-release-complete",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test/continuous/plan.json",
                "plan_sha256": "a" * 64,
                "parser_version": "test-continuous-v1",
                "throttle_seconds": 3,
            }
        )
        claim = self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            discovery["id"], "continuous-test-worker", 180
        )
        self.assertTrue(claim["claimed"])
        self.env["southern.sparex.discovery.run"].record_discovery_page(
            discovery["id"],
            "continuous-test-worker",
            {
                "page_url": seed_url,
                "page_sha256": "b" * 64,
                "artifact_uri": "s3://test/continuous/page.json",
                "artifact_sha256": "c" * 64,
                "next_url": "",
                "items": [
                    {
                        "sku": product.default_code,
                        "source_url": "https://us.sparex.com/filter-700001.html",
                        "image_url": "https://cdn.example.com/700001.jpg",
                        "source_state": "verified",
                    }
                ],
            },
        )
        self.sync.write({"mode": "sparex_discovery", "batch_size": 5})
        self.sync.action_request_approval()
        self.sync.action_approve()
        self.sync.action_enable_continuous_release()

        run_id = self.sync._run_one_batch()
        run = self.env["southern.parts.automation.run"].browse(run_id)
        self.assertEqual(run.job_type, "catalog_release")
        worker_claim = self.env["southern.parts.automation.run"].claim_queued_run(
            ["catalog_release"], "continuous-aws-worker", 3.0, 900
        )
        self.assertTrue(worker_claim["claimed"])
        self.env["southern.parts.automation.run"].finish_claimed_run(
            run.id,
            "continuous-aws-worker",
            "succeeded",
            {
                "processed_count": 1,
                "artifact_uri": "s3://test/continuous/result.json",
                "artifact_sha256": "d" * 64,
                "artifact_schema_version": "1.1",
                "archive_uri": "s3://test/continuous/result.json",
                "artifact_archived": True,
            },
        )

        self.assertEqual(self.sync.approval_state, "approved")
        self.assertTrue(self.sync.continuous_release_enabled)
        self.assertEqual(self.sync.continuous_release_batch_count, 1)
        self.assertTrue(self.sync.next_allowed_run_at)

    def test_continuous_release_stops_after_three_safety_warnings(self):
        self.sync.write({"mode": "sparex_discovery"})
        self.sync.action_request_approval()
        self.sync.action_approve()
        self.sync.action_enable_continuous_release()
        Run = self.env["southern.parts.automation.run"]
        for attempt in range(3):
            self.sync.write({"cooldown_until": False, "next_allowed_run_at": False, "state": "idle"})
            run_id = Run.queue_external_run(
                self.sync.id,
                {
                    "name": "Continuous warning %s" % attempt,
                    "idempotency_key": "continuous-warning-%s" % attempt,
                    "job_type": "sparex_discovery",
                    "mode": "evidence_only",
                    "requested_count": 5,
                    "request_json": ('{"job_type":"sparex_discovery","limit":5,"publish":false}'),
                },
            )
            Run.claim_queued_run(["sparex_discovery"], "warning-worker", 3.0, 900)
            Run.finish_claimed_run(
                run_id,
                "warning-worker",
                "blocked",
                {"error_count": 1, "cooldown_minutes": 60},
            )

        self.assertFalse(self.sync.continuous_release_enabled)
        self.assertFalse(self.sync.internal_cron_enabled)
        self.assertEqual(self.sync.state, "paused")
        self.assertEqual(self.sync.continuous_release_failure_streak, 3)

    def test_sparex_cron_does_not_queue_during_cooldown(self):
        self.sync.write(
            {
                "mode": "sparex_discovery",
                "internal_cron_enabled": True,
                "cooldown_until": "2099-01-01 00:00:00",
            }
        )
        self.assertFalse(self.sync._run_one_batch())
        self.assertFalse(self.sync.run_ids)

    def test_expired_worker_claim_is_blocked_and_released(self):
        self.sync.write({"mode": "sparex_discovery", "internal_cron_enabled": True})
        run_id = self.sync._run_one_batch()
        Run = self.env["southern.parts.automation.run"]
        Run.claim_queued_run(["sparex_discovery"], "expired-worker", 3.0, 60)
        run = Run.browse(run_id)
        run.write({"lease_expires_at": "2000-01-01 00:00:00"})
        self.assertEqual(Run.recover_expired_claims(), 1)
        self.assertEqual(run.state, "blocked")
        self.assertEqual(self.sync.state, "idle")
        self.assertTrue(self.sync.next_allowed_run_at)
        self.assertTrue(self.sync.cooldown_until)
