import base64
import hashlib
import json

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
        supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
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
        self.agent.batch_size = 50
        with self.assertRaises(ValidationError):
            self.agent.batch_size = 51
        with self.assertRaises(ValidationError):
            self.agent.throttle_seconds = 2.9

    def test_fixed_chain_publishes_only_flags_and_can_rollback(self):
        product = self.env["product.template"].create(
            {
                "name": "Release-ready Sparex part",
                "default_code": "S.880001",
                "active": True,
                "list_price": 40.0,
                "standard_price": 11.0,
                "southern_source_url": "https://us.sparex.com/example-880001.html",
                "image_1920": base64.b64encode(b"release-image"),
                "website_published": False,
            }
        )
        supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
        self.env["product.supplierinfo"].create(
            {"partner_id": supplier.id, "product_tmpl_id": product.id, "price": 15.0, "min_qty": 1.0}
        )
        agents = self.env["southern.catalog.agent"].search(
            [("company_id", "=", self.env.company.id), ("active", "=", True)]
        )
        agents.write({"ai_enabled": True})
        seeded = self.env["southern.catalog.agent.task"].seed_ready_candidates("test-worker", limit=1)
        self.assertEqual(len(seeded), 1)

        decisions = {
            "coordinator": ("continue", "sparex_discovery"),
            "sparex_discovery": ("continue", "odoo_match"),
            "odoo_match": ("continue", "product_verification"),
            "product_verification": ("ready_for_release", "website_release"),
            "website_release": ("ready_for_release", None),
        }
        for agent_code, (decision, next_agent) in decisions.items():
            claimed = self.env["southern.catalog.agent.task"].claim_tasks(agent_code, "test-worker", limit=1)
            self.assertEqual(len(claimed), 1)
            output = json.dumps(
                {
                    "decision": decision,
                    "summary": "Deterministic test decision",
                    "confidence": 1.0,
                    "blocking_reasons": [],
                    "next_agent": next_agent,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            self.env["southern.catalog.agent.task"].record_external_result(
                claimed[0]["id"], output, hashlib.sha256(output.encode()).hexdigest(), state="completed"
            )

        prepared = self.env["southern.catalog.agent.task"].prepare_publication_plan("test-worker", limit=1)
        self.assertEqual(len(prepared), 1)
        list_price = product.list_price
        standard_price = product.standard_price
        published = self.env["southern.catalog.agent.task"].publish_prepared_tasks(
            prepared,
            "test-worker",
            "catalog-agent-publication",
            "Test exact publication transaction",
        )
        self.assertEqual(len(published), 1)
        self.assertTrue(product.website_published)
        self.assertEqual(product.list_price, list_price)
        self.assertEqual(product.standard_price, standard_price)
        release_task = self.env["southern.catalog.agent.task"].browse(published[0]["task_id"])
        self.env["southern.catalog.agent.task"].rollback_publications(
            [release_task.id], "Synthetic verification rollback"
        )
        self.assertFalse(product.website_published)
        self.assertEqual(product.list_price, list_price)
        self.assertEqual(product.standard_price, standard_price)
