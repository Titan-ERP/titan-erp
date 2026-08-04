from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("at_install", "-post_install")
class TestAutomationControl(TransactionCase):
    def setUp(self):
        super().setUp()
        self.sync = self.env["southern.parts.catalog.sync"].create(
            {"name": "Test controlled catalog workflow"}
        )

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
        run_id = self.sync.action_queue_approved_apply() and self.sync.run_ids.filtered(
            lambda row: row.job_type == "catalog_release"
        ).id
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
