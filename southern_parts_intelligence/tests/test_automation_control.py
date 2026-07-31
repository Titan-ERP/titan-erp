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
