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
        self.website_category = self.env["product.public.category"].create({"name": "Test Parts"})
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

    def _record_current_discovery(self, product, source_url):
        Agent = self.env["southern.catalog.agent"]
        for code, name in (("sparex_discovery", "Sparex Discovery Agent"), ("odoo_match", "Odoo Match Agent")):
            if not Agent.search([("code", "=", code), ("company_id", "=", self.env.company.id)], limit=1):
                Agent.create(
                    {
                        "name": name,
                        "code": code,
                        "company_id": self.env.company.id,
                        "instructions": "Use exact deterministic catalog facts.",
                    }
                )
        seed_url = "https://us.sparex.com/test-listing.html"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": f"test-current-evidence-{product.id}",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": f"s3://test-bucket/discovery/{product.id}/plan.json",
                "plan_sha256": hashlib.sha256(f"plan-{product.id}".encode()).hexdigest(),
                "parser_version": "test-current-v3",
                "throttle_seconds": 3,
            }
        )
        self.env["southern.sparex.discovery.run"].prepare_reconciliation_run(run["id"])
        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(run["id"], "test-worker", 180)
        self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "test-worker",
            {
                "page_url": seed_url,
                "page_sha256": hashlib.sha256(f"page-{product.id}".encode()).hexdigest(),
                "artifact_uri": f"s3://test-bucket/discovery/{product.id}/page.json",
                "artifact_sha256": hashlib.sha256(f"artifact-{product.id}".encode()).hexdigest(),
                "next_url": "",
                "listing_urls": [],
                "items": [
                    {
                        "sku": product.default_code,
                        "source_url": source_url,
                        "image_url": f"https://cdn.example.com/{product.id}.jpg",
                        "source_state": "verified",
                    }
                ],
            },
        )

    def test_ready_snapshot_requires_customer_ready_product_data(self):
        product = self.env["product.template"].create(
            {
                "name": "Ready Sparex part",
                "default_code": "S.00146",
                "active": True,
                "list_price": 25.0,
                "southern_source_url": "https://us.sparex.com/example-146.html",
                "image_1920": base64.b64encode(b"test-image"),
                "public_categ_ids": [(6, 0, self.website_category.ids)],
                "description_ecommerce": "Customer-ready replacement part description.",
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
        self._record_current_discovery(product, "https://us.sparex.com/example-146.html")
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

    def test_native_publication_gate_blocks_placeholder_and_below_cost_prices(self):
        product = self.env["product.template"].create(
            {
                "name": "Guarded Sparex part",
                "default_code": "S.880000",
                "active": True,
                "list_price": 1.0,
                "southern_source_url": "https://us.sparex.com/example-880000.html",
                "image_1920": base64.b64encode(b"guarded-image"),
                "public_categ_ids": [(6, 0, self.website_category.ids)],
                "description_ecommerce": "Customer-ready replacement part description.",
                "website_published": False,
            }
        )
        supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
        self.env["product.supplierinfo"].create(
            {"partner_id": supplier.id, "product_tmpl_id": product.id, "price": 5.0, "min_qty": 1.0}
        )
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            product.website_published = True

    def test_native_publication_gate_allows_verified_low_cost_plus_price(self):
        product = self.env["product.template"].create(
            {
                "name": "Low-cost guarded Sparex part",
                "default_code": "S.880001",
                "active": True,
                "list_price": 1.13,
                "southern_price_basis": "cost_plus",
                "southern_cost_plus_margin_percent": 35.0,
                "southern_source_url": "https://us.sparex.com/example-880001.html",
                "image_1920": base64.b64encode(b"low-cost-image"),
                "public_categ_ids": [(6, 0, self.website_category.ids)],
                "description_ecommerce": "Customer-ready low-cost replacement part description.",
                "website_published": False,
            }
        )
        supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
        self.env["product.supplierinfo"].create(
            {"partner_id": supplier.id, "product_tmpl_id": product.id, "price": 0.70, "min_qty": 1.0}
        )

        product.website_published = True

        self.assertTrue(product.website_published)

    def test_native_publication_gate_allows_evidence_complete_quote_only_product(self):
        product = self.env["product.template"].create(
            {
                "name": "Quote-only Sparex part",
                "default_code": "S.880009",
                "active": True,
                "list_price": 0.0,
                "southern_quote_only": True,
                "southern_source_url": "https://us.sparex.com/example-880009.html",
                "image_1920": base64.b64encode(b"quote-image"),
                "public_categ_ids": [(6, 0, self.website_category.ids)],
                "description_ecommerce": "Customer-ready quote-only replacement part.",
                "website_published": False,
            }
        )
        product.website_published = True
        self.assertTrue(product.website_published)
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            product.list_price = 10.0
        product.invalidate_recordset()
        product.list_price = 4.99
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            product.website_published = True
        product.invalidate_recordset()
        product.list_price = 9.99
        product.description_ecommerce = (
            "Internal catalog record. Not published to the website until pricing is reviewed."
        )
        with self.assertRaises(ValidationError), self.env.cr.savepoint():
            product.website_published = True

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
                "public_categ_ids": [(6, 0, self.website_category.ids)],
                "description_ecommerce": "Customer-ready release description.",
                "website_published": False,
            }
        )
        supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
        self.env["product.supplierinfo"].create(
            {"partner_id": supplier.id, "product_tmpl_id": product.id, "price": 15.0, "min_qty": 1.0}
        )
        self._record_current_discovery(product, "https://us.sparex.com/example-880001.html")
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
