import base64

from odoo.exceptions import ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("at_install", "-post_install")
class TestCatalogAgents(TransactionCase):
    def setUp(self):
        super().setUp()
        self.agent = self.env["southern.catalog.agent"].search(
            [
                ("code", "=", "product_verification"),
                ("company_id", "=", self.env.company.id),
            ],
            limit=1,
        )
        if not self.agent:
            self.agent = self.env["southern.catalog.agent"].create(
                {
                    "name": "Test Product Verification Agent",
                    "code": "product_verification",
                    "company_id": self.env.company.id,
                    "instructions": "Use deterministic readiness facts only.",
                }
            )

    def test_ready_snapshot_uses_only_four_business_requirements(self):
        product = self.env["product.template"].create(
            {
                "name": "Ready Sparex part",
                "default_code": "S.00146",
                "active": True,
                "list_price": 25.0,
                "southern_source_url": "https://us.sparex.com/example-146.html",
                "image_1920": base64.b64encode(b"test-image"),
                "website_published": False,
            }
        )
        supplier = self.env["res.partner"].create(
            {"name": "Sparex Test Supplier", "supplier_rank": 1}
        )
        self.env["product.supplierinfo"].create(
            {
                "partner_id": supplier.id,
                "product_tmpl_id": product.id,
                "price": 10.0,
                "min_qty": 1.0,
            }
        )
        task_id = self.env["southern.catalog.agent.task"].queue_candidate(
            "product_verification",
            "S.146",
            {"idempotency_key": "catalog-agent-ready-146"},
        )
        task = self.env["southern.catalog.agent.task"].browse(task_id)
        self.assertEqual(task.odoo_match_state, "matched")
        self.assertEqual(task.product_tmpl_id, product)
        self.assertTrue(task.has_positive_supplier_cost)
        self.assertTrue(task.has_positive_sales_price)
        self.assertTrue(task.has_exact_sparex_url)
        self.assertTrue(task.has_image)
        self.assertTrue(task.ready_to_publish)

        product.southern_source_url = "https://us.sparex.com/example-999.html"
        task.action_prepare_snapshot()
        self.assertFalse(task.has_exact_sparex_url)
        self.assertFalse(task.ready_to_publish)
        self.assertIn("missing_exact_sparex_url", task.readiness_blockers)

    def test_missing_sku_is_recorded_without_product_creation(self):
        before = self.env["product.template"].search_count([])
        task_id = self.env["southern.catalog.agent.task"].queue_candidate(
            "product_verification",
            "S.999999",
            {"idempotency_key": "catalog-agent-missing-999999"},
        )
        task = self.env["southern.catalog.agent.task"].browse(task_id)
        self.assertEqual(task.odoo_match_state, "missing")
        self.assertFalse(task.product_tmpl_id)
        self.assertFalse(task.ready_to_publish)
        self.assertEqual(self.env["product.template"].search_count([]), before)

    def test_batch_and_throttle_safety_floors(self):
        with self.assertRaises(ValidationError):
            self.agent.batch_size = 6
        with self.assertRaises(ValidationError):
            self.agent.throttle_seconds = 2.9
