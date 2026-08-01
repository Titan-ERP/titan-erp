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
        self.assertEqual(by_sku["S.999999"].creation_state, "not_authorized")
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
