import base64
import hashlib

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("at_install", "-post_install")
class TestSparexDiscovery(TransactionCase):
    def setUp(self):
        super().setUp()
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

    def test_page_inventory_classifies_existing_and_missing_without_product_creation(self):
        existing = self.env["product.template"].create(
            {"name": "Existing catalog part", "default_code": "S.165551", "active": True}
        )
        product_count = self.env["product.template"].with_context(active_test=False).search_count([])
        seed_url = "https://us.sparex.com/products?p=1"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-full-catalog-v1",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/plan.json",
                "plan_sha256": "a" * 64,
                "parser_version": "test-listing-v1",
                "throttle_seconds": 3,
            }
        )
        claim = self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(run["id"], "test-worker", 180)
        self.assertTrue(claim["claimed"])
        result = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "test-worker",
            {
                "page_url": seed_url,
                "page_sha256": "b" * 64,
                "artifact_uri": "s3://test-bucket/discovery/listing-page.json",
                "artifact_sha256": "c" * 64,
                "next_url": "",
                "items": [
                    {
                        "sku": "S.165551",
                        "source_url": "https://us.sparex.com/filter-165551.html",
                        "image_url": "https://cdn.example.com/165551.jpg",
                        "source_state": "verified",
                    },
                    {
                        "sku": "S.999999",
                        "source_url": "https://us.sparex.com/filter-999999.html",
                        "image_url": "https://cdn.example.com/999999.jpg",
                        "source_state": "verified",
                    },
                ],
            },
        )
        self.assertEqual(result["state"], "completed")
        items = self.env["southern.sparex.discovery.item"].search([("normalized_sku", "in", ["S.165551", "S.999999"])])
        by_sku = {item.normalized_sku: item for item in items}
        self.assertEqual(by_sku["S.165551"].odoo_match_state, "matched_active")
        self.assertEqual(by_sku["S.165551"].matched_product_id, existing)
        self.assertFalse(by_sku["S.165551"].has_positive_supplier_cost)
        self.assertFalse(by_sku["S.165551"].publication_candidate)
        self.assertEqual(by_sku["S.999999"].odoo_match_state, "missing")
        self.assertEqual(by_sku["S.999999"].creation_state, "review_required")
        self.assertEqual(self.env["product.template"].with_context(active_test=False).search_count([]), product_count)

    def test_listing_frontier_advances_without_opening_product_pages(self):
        seed_url = "https://us.sparex.com/"
        category_url = "https://us.sparex.com/engine-filters.html"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-frontier-v2",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/frontier-plan.json",
                "plan_sha256": "d" * 64,
                "parser_version": "test-listing-frontier-v2",
                "throttle_seconds": 3,
                "max_pages_total": 10,
            }
        )
        first_claim = self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run["id"], "test-frontier-worker", 180
        )
        self.assertEqual(first_claim["cursor_url"], seed_url)
        first_result = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "test-frontier-worker",
            {
                "page_url": seed_url,
                "page_sha256": "e" * 64,
                "artifact_uri": "s3://test-bucket/discovery/frontier-page-1.json",
                "artifact_sha256": "f" * 64,
                "items": [],
                "next_url": "",
                "listing_urls": [category_url],
            },
        )
        self.assertEqual(first_result["state"], "ready")
        active_run = self.env["southern.sparex.discovery.run"].browse(run["id"])
        self.assertEqual(active_run.cursor_url, category_url)
        self.assertEqual(active_run.queued_url_count, 1)
        self.assertEqual(active_run.visited_url_count, 1)

        second_claim = self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run["id"], "test-frontier-worker", 180
        )
        self.assertEqual(second_claim["cursor_url"], category_url)
        second_result = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "test-frontier-worker",
            {
                "page_url": category_url,
                "page_sha256": "1" * 64,
                "artifact_uri": "s3://test-bucket/discovery/frontier-page-2.json",
                "artifact_sha256": "2" * 64,
                "items": [],
                "next_url": "",
                "listing_urls": [seed_url, category_url],
            },
        )
        self.assertEqual(second_result["state"], "completed")
        self.assertEqual(active_run.page_count, 2)
        self.assertEqual(active_run.visited_url_count, 2)
        self.assertEqual(active_run.queued_url_count, 0)

    def test_current_run_corrects_old_review_and_marks_unseen_items_stale(self):
        old_seed = "https://us.sparex.com/old-listing.html"
        old_run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-reconciliation-old-v2",
                "seed_url": old_seed,
                "seed_url_sha256": hashlib.sha256(old_seed.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/old-plan.json",
                "plan_sha256": "6" * 64,
                "parser_version": "test-v2",
                "throttle_seconds": 3,
            }
        )
        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(old_run["id"], "old-worker", 180)
        self.env["southern.sparex.discovery.run"].record_discovery_page(
            old_run["id"],
            "old-worker",
            {
                "page_url": old_seed,
                "page_sha256": "7" * 64,
                "artifact_uri": "s3://test-bucket/discovery/old-page.json",
                "artifact_sha256": "8" * 64,
                "items": [
                    {
                        "sku": "S.700001",
                        "source_url": "https://us.sparex.com/part-700001.html",
                        "image_url": "https://cdn.example.com/700001.jpg",
                        "source_state": "ambiguous",
                    },
                    {
                        "sku": "S.700002",
                        "source_url": "https://us.sparex.com/part-700002.html",
                        "image_url": "https://cdn.example.com/700002.jpg",
                        "source_state": "verified",
                    },
                ],
                "next_url": "",
                "listing_urls": [],
            },
        )

        seed = "https://us.sparex.com/current-listing.html"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-reconciliation-current-v3",
                "seed_url": seed,
                "seed_url_sha256": hashlib.sha256(seed.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/current-plan.json",
                "plan_sha256": "9" * 64,
                "parser_version": "test-v3",
                "throttle_seconds": 3,
                "max_pages_per_checkpoint": 5,
            }
        )
        self.env["southern.sparex.discovery.run"].prepare_reconciliation_run(run["id"])
        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(run["id"], "current-worker", 180)
        result = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "current-worker",
            {
                "page_url": seed,
                "page_sha256": "a" * 64,
                "artifact_uri": "s3://test-bucket/discovery/current-page.json",
                "artifact_sha256": "b" * 64,
                "items": [
                    {
                        "sku": "S.700001",
                        "source_url": "https://us.sparex.com/part-700001.html",
                        "image_url": "https://cdn.example.com/700001.jpg",
                        "source_state": "verified",
                    }
                ],
                "next_url": "",
                "listing_urls": [],
            },
        )
        corrected = self.env["southern.sparex.discovery.item"].search([("normalized_sku", "=", "S.700001")])
        stale = self.env["southern.sparex.discovery.item"].search([("normalized_sku", "=", "S.700002")])
        active_run = self.env["southern.sparex.discovery.run"].browse(run["id"])
        self.assertEqual(result["corrected"], 1)
        self.assertEqual(corrected.reconciliation_state, "current")
        self.assertEqual(corrected.correction_count, 1)
        self.assertEqual(stale.reconciliation_state, "stale")
        self.assertEqual(stale.review_reason, "stale_not_seen")
        self.assertEqual(active_run.reconciliation_state, "completed")
        self.assertEqual(active_run.stale_count, 1)

    def test_verified_source_link_is_planned_applied_and_reversible(self):
        product = self.env["product.template"].create(
            {
                "name": "Source-link candidate",
                "default_code": "S.710001",
                "active": True,
                "list_price": 30.0,
                "image_1920": base64.b64encode(b"product-image"),
                "website_published": False,
            }
        )
        supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
        self.env["product.supplierinfo"].create(
            {"partner_id": supplier.id, "product_tmpl_id": product.id, "price": 12.0, "min_qty": 1.0}
        )
        seed = "https://us.sparex.com/source-link-listing.html"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-source-link-v3",
                "seed_url": seed,
                "seed_url_sha256": hashlib.sha256(seed.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/source-link-plan.json",
                "plan_sha256": "c" * 64,
                "parser_version": "test-v3",
                "throttle_seconds": 3,
            }
        )
        Run = self.env["southern.sparex.discovery.run"]
        Run.prepare_reconciliation_run(run["id"])
        Run.claim_discovery_checkpoint(run["id"], "source-link-worker", 180)
        Run.record_discovery_page(
            run["id"],
            "source-link-worker",
            {
                "page_url": seed,
                "page_sha256": "d" * 64,
                "artifact_uri": "s3://test-bucket/discovery/source-link-page.json",
                "artifact_sha256": "e" * 64,
                "items": [
                    {
                        "sku": "S.710001",
                        "source_url": "https://us.sparex.com/part-710001.html",
                        "image_url": "https://cdn.example.com/710001.jpg",
                        "source_state": "verified",
                    }
                ],
                "next_url": "",
                "listing_urls": [],
            },
        )
        Item = self.env["southern.sparex.discovery.item"]
        plan = Item.prepare_source_link_plan(limit=5)
        self.assertEqual(len(plan), 1)
        applied = Item.apply_source_link_plan(
            plan, "sparex-discovery-source-link", "Test exact verified source linking"
        )
        self.assertEqual(product.southern_source_url, "https://us.sparex.com/part-710001.html")
        item = Item.browse(applied[0]["item_id"])
        self.assertTrue(item.publication_candidate)
        Item.rollback_source_links(applied, "Synthetic source-link rollback")
        self.assertFalse(product.southern_source_url)

    def test_large_listing_frontier_remains_bounded_and_resumable(self):
        seed_url = "https://us.sparex.com/"
        listing_urls = [f"https://us.sparex.com/category-{index}-parts.html" for index in range(501)]
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-large-frontier-v2",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/large-frontier-plan.json",
                "plan_sha256": "3" * 64,
                "parser_version": "test-listing-frontier-v2",
                "throttle_seconds": 3,
                "max_pages_total": 600,
            }
        )
        claim = self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run["id"], "test-large-frontier-worker", 180
        )
        self.assertTrue(claim["claimed"])
        result = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "test-large-frontier-worker",
            {
                "page_url": seed_url,
                "page_sha256": "4" * 64,
                "artifact_uri": "s3://test-bucket/discovery/large-frontier-page.json",
                "artifact_sha256": "5" * 64,
                "items": [],
                "next_url": "",
                "listing_urls": listing_urls,
            },
        )
        active_run = self.env["southern.sparex.discovery.run"].browse(run["id"])
        self.assertEqual(result["state"], "ready")
        self.assertEqual(active_run.queued_url_count, 501)
        self.assertEqual(active_run.visited_url_count, 1)
