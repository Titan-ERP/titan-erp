import base64
import hashlib
from unittest.mock import patch

from odoo import fields
from odoo.addons.southern_parts_intelligence.models.sparex_discovery import _verified_detail_title
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("at_install", "-post_install")
class TestSparexDiscovery(TransactionCase):
    def setUp(self):
        super().setUp()
        self.website_category = self.env["product.public.category"].create({"name": "Test Parts"})
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

    def test_verified_detail_title_rejects_scraped_browser_code(self):
        contaminated = (
            ".product-image-container-9730 { width: 295px; } "
            'document.querySelectorAll(".product-image-container-9730")'
        )
        self.assertFalse(_verified_detail_title(contaminated))

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

    def test_page_inventory_bulk_match_preserves_case_insensitive_duplicates(self):
        self.env["product.template"].create(
            {"name": "First duplicate", "default_code": "s.765430", "active": True}
        )
        self.env["product.template"].create(
            {"name": "Second duplicate", "default_code": "S765430", "active": False}
        )
        seed_url = "https://us.sparex.com/products?p=bulk"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-bulk-product-match",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/bulk-plan.json",
                "plan_sha256": "1" * 64,
                "parser_version": "test-listing-v1",
                "throttle_seconds": 3,
            }
        )
        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run["id"], "bulk-test-worker", 180
        )
        result = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "bulk-test-worker",
            {
                "page_url": seed_url,
                "page_sha256": "2" * 64,
                "artifact_uri": "s3://test-bucket/discovery/bulk-page.json",
                "artifact_sha256": "3" * 64,
                "next_url": "",
                "items": [
                    {
                        "sku": "S.765430",
                        "source_url": "https://us.sparex.com/duplicate-765430.html",
                        "image_url": "https://cdn.example.com/765430.jpg",
                        "source_state": "verified",
                    },
                    {
                        "sku": "S.765431",
                        "source_url": "https://us.sparex.com/missing-765431.html",
                        "image_url": "https://cdn.example.com/765431.jpg",
                        "source_state": "verified",
                    },
                ],
            },
        )
        items = self.env["southern.sparex.discovery.item"].browse(result["item_ids"])
        by_sku = {item.normalized_sku: item for item in items}
        self.assertEqual(by_sku["S.765430"].odoo_match_state, "duplicate")
        self.assertEqual(len(by_sku["S.765430"].duplicate_product_ids), 2)
        self.assertEqual(by_sku["S.765431"].odoo_match_state, "missing")

    def test_dashboard_uses_live_product_publication_instead_of_stale_item_flag(self):
        product = self.env["product.template"].create(
            {"name": "Live publication state test", "default_code": "S.165552", "active": True}
        )
        seed_url = "https://us.sparex.com/products?p=1"
        run_values = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-live-dashboard-publication-state",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/live-dashboard-plan.json",
                "plan_sha256": "d" * 64,
                "parser_version": "test-listing-v1",
                "throttle_seconds": 3,
            }
        )
        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run_values["id"], "dashboard-test-worker", 180
        )
        self.env["southern.sparex.discovery.run"].record_discovery_page(
            run_values["id"],
            "dashboard-test-worker",
            {
                "page_url": seed_url,
                "page_sha256": "e" * 64,
                "artifact_uri": "s3://test-bucket/discovery/live-dashboard-page.json",
                "artifact_sha256": "f" * 64,
                "next_url": "",
                "items": [
                    {
                        "sku": "S.165552",
                        "source_url": "https://us.sparex.com/filter-165552.html",
                        "image_url": "https://cdn.example.com/165552.jpg",
                        "source_state": "verified",
                    }
                ],
            },
        )
        run = self.env["southern.sparex.discovery.run"].browse(run_values["id"])
        item = self.env["southern.sparex.discovery.item"].search(
            [("last_seen_run_id", "=", run.id), ("normalized_sku", "=", "S.165552")]
        )

        item.currently_published = True
        run.invalidate_recordset(["published_product_count", "blocked_item_count"])
        self.assertEqual(run.published_product_count, 0)

        product.website_published = True
        item.currently_published = False
        run.invalidate_recordset(["published_product_count", "blocked_item_count"])
        self.assertEqual(run.published_product_count, 1)

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

    def test_targeted_repair_preempts_frontier_without_recounting_catalog_page(self):
        seed_url = "https://us.sparex.com/"
        category_url = "https://us.sparex.com/engine-filters.html"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-targeted-repair-frontier",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/repair-plan.json",
                "plan_sha256": "4" * 64,
                "parser_version": "test-listing-v6",
                "throttle_seconds": 3,
            }
        )
        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run["id"], "repair-test-worker", 180
        )
        self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "repair-test-worker",
            {
                "page_url": seed_url,
                "page_sha256": "5" * 64,
                "artifact_uri": "s3://test-bucket/discovery/repair-page-old.json",
                "artifact_sha256": "6" * 64,
                "items": [
                    {
                        "sku": "S.765432",
                        "listing_title": "Malformed legacy title",
                        "source_url": "https://us.sparex.com/part-765432.html",
                        "image_url": "",
                        "source_state": "ambiguous",
                    }
                ],
                "listing_urls": [category_url],
            },
        )
        active_run = self.env["southern.sparex.discovery.run"].browse(run["id"])
        self.assertEqual(active_run.cursor_url, category_url)
        queued = self.env["southern.sparex.discovery.run"].queue_discovery_page_repairs(
            run["id"], [seed_url], "Reparse legacy listing evidence"
        )
        self.assertEqual(queued["cursor_kind"], "repair")
        self.assertEqual(active_run.cursor_url, seed_url)
        self.assertEqual(active_run.repair_queued_url_count, 1)
        queued_again = self.env["southern.sparex.discovery.run"].queue_discovery_page_repairs(
            run["id"], [seed_url], "Reparse legacy listing evidence"
        )
        self.assertEqual(queued_again["repair_queued_url_count"], 1)
        self.assertEqual(active_run.queued_url_count, 1)

        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run["id"], "repair-test-worker", 180
        )
        repaired = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "repair-test-worker",
            {
                "page_url": seed_url,
                "page_sha256": "7" * 64,
                "artifact_uri": "s3://test-bucket/discovery/repair-page-new.json",
                "artifact_sha256": "8" * 64,
                "items": [
                    {
                        "sku": "S.765432",
                        "listing_title": "Verified Repair Part",
                        "source_url": "https://us.sparex.com/part-765432.html",
                        "image_url": "https://cdn.example.com/765432.jpg",
                        "source_state": "verified",
                    }
                ],
                "listing_urls": [category_url],
            },
        )
        item = self.env["southern.sparex.discovery.item"].browse(repaired["item_ids"])
        page = self.env["southern.sparex.discovery.page"].search(
            [("run_id", "=", active_run.id), ("page_url_sha256", "=", hashlib.sha256(seed_url.encode()).hexdigest())]
        )
        self.assertTrue(repaired["repair"])
        self.assertEqual(active_run.page_count, 1)
        self.assertEqual(active_run.cursor_url, category_url)
        self.assertEqual(active_run.cursor_kind, "frontier")
        self.assertEqual(active_run.repair_queued_url_count, 0)
        self.assertEqual(active_run.repair_visited_url_count, 1)
        self.assertEqual(page.repair_visit_count, 1)
        self.assertEqual(item.state, "verified")
        self.assertEqual(item.listing_title, "Verified Repair Part")

    def test_page_driven_creation_makes_one_categorized_unpublished_draft(self):
        supplier = self.env["res.partner"].search([("name", "=ilike", "Sparex")])
        self.assertLessEqual(len(supplier), 1)
        if supplier:
            supplier.supplier_rank = 1
        else:
            supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
        sync = self.env["southern.parts.catalog.sync"].create(
            {"name": "Page-driven creation test", "mode": "sparex_discovery", "batch_size": 5}
        )
        sync.action_request_approval()
        sync.action_approve()
        sync.action_enable_continuous_release()
        sync.action_enable_page_driven_creation()
        seed_url = "https://us.sparex.com/products?p=1"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-page-driven-product-creation",
                "seed_url": seed_url,
                "seed_url_sha256": hashlib.sha256(seed_url.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/plan.json",
                "plan_sha256": "a" * 64,
                "parser_version": "test-listing-v4",
                "throttle_seconds": 3,
            }
        )
        self.env["southern.sparex.discovery.run"].claim_discovery_checkpoint(
            run["id"], "creation-test-worker", 180
        )
        recorded = self.env["southern.sparex.discovery.run"].record_discovery_page(
            run["id"],
            "creation-test-worker",
            {
                "page_url": seed_url,
                "page_sha256": "b" * 64,
                "artifact_uri": "s3://test-bucket/discovery/listing-page.json",
                "artifact_sha256": "c" * 64,
                "items": [
                    {
                        "sku": "S.999998",
                        "listing_title": "Hydraulic Filter",
                        "source_url": "https://us.sparex.com/hydraulic-filter-999998.html",
                        "image_url": "https://cdn.example.com/999998.jpg",
                        "source_state": "verified",
                    }
                ],
                "next_url": "",
            },
        )
        plans = self.env["southern.sparex.discovery.item"].prepare_product_creation_plan(
            recorded["item_ids"], limit=5
        )
        self.assertEqual(len(plans), 1)
        applied = self.env["southern.sparex.discovery.item"].apply_product_creation_plan(
            plans,
            "s3://test-bucket/discovery/product-creation-plan.json",
            "d" * 64,
            "sparex-page-driven-draft-creation",
            "Create exact listing-page product as an unpublished draft",
        )
        self.assertEqual(len(applied), 1)
        self.assertTrue(applied[0]["created"])
        product = self.env["product.template"].browse(applied[0]["product_id"])
        item = self.env["southern.sparex.discovery.item"].browse(recorded["item_ids"][0])
        self.assertEqual(product.default_code, "S.999998")
        self.assertEqual(product.name, "Hydraulic Filter")
        self.assertEqual(
            product.categ_id,
            self.env.ref("southern_parts_intelligence.product_category_sparex_pending_enrichment"),
        )
        self.assertEqual(product.southern_source_url, "https://us.sparex.com/hydraulic-filter-999998.html")
        self.assertFalse(product.website_published)
        self.assertEqual(product.list_price, 0.0)
        self.assertEqual(product.standard_price, 0.0)
        vendor = self.env["product.supplierinfo"].search([("product_tmpl_id", "=", product.id)])
        self.assertEqual(len(vendor), 1)
        self.assertEqual(vendor.partner_id, supplier)
        self.assertEqual(vendor.price, 0.0)
        self.assertEqual(item.matched_product_id, product)
        self.assertEqual(item.creation_state, "created")
        self.assertEqual(item.primary_blocker, "missing_cost")
        self.assertEqual(
            self.env["southern.sparex.discovery.item"].prepare_product_creation_plan([item.id], limit=5),
            [],
        )
        rolled_back = self.env["southern.sparex.discovery.item"].rollback_created_products(
            applied, "Rollback unchanged unpublished test draft"
        )
        self.assertEqual(rolled_back, [{"item_id": item.id, "product_id": product.id, "active": False}])
        self.assertFalse(product.active)
        self.assertEqual(item.odoo_match_state, "matched_archived")
        self.assertEqual(item.creation_state, "rejected")

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
                "list_price": 0.0,
                "image_1920": base64.b64encode(b"product-image"),
                "public_categ_ids": [(6, 0, self.website_category.ids)],
                "description_ecommerce": "Customer-ready source-link description.",
                "website_published": False,
            }
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
                        "listing_title": "Source Link Service Part",
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
        self.assertFalse(item.publication_candidate)
        self.assertEqual(item.cost_recovery_state, "queued")

        placeholder = "Internal catalog record. Not published to the website until pricing is reviewed."
        product.write(
            {
                "description_ecommerce": placeholder,
                "website_description": placeholder,
                "description_sale": placeholder,
            }
        )
        item._refresh_readiness()
        self.assertEqual(item.primary_blocker, "missing_customer_description")
        search_calls = []
        original_search = type(Item).search

        def capture_search(recordset, domain, *args, **kwargs):
            search_calls.append((domain, kwargs))
            return original_search(recordset, domain, *args, **kwargs)

        with patch.object(type(Item), "search", capture_search):
            description_plan = Item.prepare_description_repair_plan(limit=5)
        self.assertIn(
            ("primary_blocker", "in", ("missing_customer_description", "already_published")),
            search_calls[0][0],
        )
        self.assertEqual(search_calls[0][1]["limit"], 20)
        self.assertEqual(len(description_plan), 1)
        repaired_descriptions = Item.apply_description_repair_plan(
            description_plan,
            "sparex-listing-description-repair",
            "Repair verified listing placeholder copy",
        )
        self.assertIn("Source Link Service Part", product.description_sale)
        self.assertFalse(item.publication_candidate)
        Item.rollback_description_repairs(repaired_descriptions, "Synthetic description rollback")
        self.assertEqual(product.description_sale, placeholder)
        Item.rollback_source_links(applied, "Synthetic source-link rollback")
        self.assertFalse(product.southern_source_url)

        product.image_1920 = False
        item._refresh_readiness()
        image_plan = Item.prepare_source_link_plan(limit=5)
        self.assertEqual(len(image_plan), 1)
        image_bytes = b"listing-image-repair"
        image_plan[0]["image_base64"] = base64.b64encode(image_bytes).decode("ascii")
        image_plan[0]["image_content_sha256"] = hashlib.sha256(image_bytes).hexdigest()
        repaired = Item.apply_source_link_plan(
            image_plan, "sparex-discovery-source-link", "Test exact URL and image repair"
        )
        self.assertTrue(product.image_1920)
        self.assertFalse(item.publication_candidate)
        Item.rollback_source_links(repaired, "Synthetic URL and image rollback")
        self.assertFalse(product.southern_source_url)
        self.assertFalse(product.image_1920)

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

    def test_missing_cost_enters_prioritized_recovery_and_schedules_retry(self):
        product = self.env["product.template"].create(
            {
                "name": "Cost recovery candidate",
                "default_code": "S.720001",
                "active": True,
                "list_price": 45.0,
                "southern_source_url": "https://us.sparex.com/part-720001.html",
                "image_1920": base64.b64encode(b"cost-recovery-image"),
                "website_published": False,
            }
        )
        seed = "https://us.sparex.com/cost-recovery-listing.html"
        run = self.env["southern.sparex.discovery.run"].start_discovery_run(
            {
                "idempotency_key": "test-cost-recovery-v1",
                "seed_url": seed,
                "seed_url_sha256": hashlib.sha256(seed.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/cost-recovery-plan.json",
                "plan_sha256": "f" * 64,
                "parser_version": "test-v3",
                "throttle_seconds": 3,
            }
        )
        Run = self.env["southern.sparex.discovery.run"]
        Run.prepare_reconciliation_run(run["id"])
        Run.claim_discovery_checkpoint(run["id"], "cost-recovery-discovery", 180)
        Run.record_discovery_page(
            run["id"],
            "cost-recovery-discovery",
            {
                "page_url": seed,
                "page_sha256": "1" * 64,
                "artifact_uri": "s3://test-bucket/discovery/cost-recovery-page.json",
                "artifact_sha256": "2" * 64,
                "items": [
                    {
                        "sku": "S.720001",
                        "source_url": "https://us.sparex.com/part-720001.html",
                        "image_url": "https://cdn.example.com/720001.jpg",
                        "source_state": "verified",
                    }
                ],
                "next_url": "",
                "listing_urls": [],
            },
        )
        Item = self.env["southern.sparex.discovery.item"]
        item = Item.search([("normalized_sku", "=", "S.720001")])
        item._refresh_readiness()
        self.assertEqual(item.primary_blocker, "missing_cost")
        self.assertEqual(item.cost_recovery_state, "queued")
        self.assertEqual(item.cost_recovery_priority, 160)
        claimed = Item.claim_cost_recovery_batch("test-cost-worker", limit=1)
        self.assertEqual(claimed[0]["product_id"], product.id)
        self.assertEqual(claimed[0]["attempt"], 1)
        Item.record_cost_recovery_result(
            item.id, "test-cost-worker", "not_found", error_code="verified_cost_absent"
        )
        self.assertEqual(item.cost_recovery_state, "retry_wait")
        self.assertTrue(item.cost_recovery_next_at)
        self.assertEqual(item.cost_recovery_last_error, "verified_cost_absent")

    def test_exact_dealer_cost_apply_and_scoped_rollback(self):
        product = self.env["product.template"].create(
            {
                "name": "Recoverable dealer cost",
                "default_code": "S.720002",
                "active": True,
                "list_price": 0.0,
                "southern_quote_only": True,
                "southern_source_url": "https://us.sparex.com/part-720002.html",
                "image_1920": base64.b64encode(b"cost-recovery-image"),
                "public_categ_ids": [(6, 0, self.website_category.ids)],
                "description_ecommerce": "Customer-ready dealer-cost description.",
                "website_published": False,
            }
        )
        supplier = self.env["res.partner"].create({"name": "Sparex", "supplier_rank": 1})
        supplierinfo = self.env["product.supplierinfo"].create(
            {"partner_id": supplier.id, "product_tmpl_id": product.id, "price": 0.0}
        )
        seed = "https://us.sparex.com/cost-recovery-listing-2.html"
        Run = self.env["southern.sparex.discovery.run"]
        run = Run.start_discovery_run(
            {
                "idempotency_key": "test-cost-recovery-apply-v1",
                "seed_url": seed,
                "seed_url_sha256": hashlib.sha256(seed.encode()).hexdigest(),
                "plan_artifact_uri": "s3://test-bucket/discovery/cost-recovery-apply-plan.json",
                "plan_sha256": "a" * 64,
                "parser_version": "test-v3",
                "throttle_seconds": 3,
            }
        )
        Run.prepare_reconciliation_run(run["id"])
        Run.claim_discovery_checkpoint(run["id"], "cost-recovery-apply-discovery", 180)
        Run.record_discovery_page(
            run["id"],
            "cost-recovery-apply-discovery",
            {
                "page_url": seed,
                "page_sha256": "b" * 64,
                "artifact_uri": "s3://test-bucket/discovery/cost-recovery-apply-page.json",
                "artifact_sha256": "c" * 64,
                "items": [
                    {
                        "sku": "S.720002",
                        "source_url": "https://us.sparex.com/part-720002.html",
                        "image_url": "https://cdn.example.com/720002.jpg",
                        "source_state": "verified",
                    }
                ],
                "next_url": "",
                "listing_urls": [],
            },
        )
        Item = self.env["southern.sparex.discovery.item"]
        item = Item.search([("normalized_sku", "=", "S.720002")])
        item._refresh_readiness()
        claim = Item.claim_cost_recovery_batch("cost-apply-worker", limit=1)[0]
        claim.update(
            {
                "dealer_price": 14.99,
                "currency": "USD",
                "evidence_url": claim["source_url"],
                "evidence_url_sha256": claim["source_url_sha256"],
                "evidence_sha256": "d" * 64,
                "detail_title": "Verified Hydraulic Part",
                "detail_title_sha256": hashlib.sha256(b"Verified Hydraulic Part").hexdigest(),
                "detail_title_page_sha256": "d" * 64,
                "detail_page_artifact_uri": "s3://test-bucket/detail/S-720002.html",
                "detail_page_artifact_sha256": "d" * 64,
                "parser_version": "sparex-exact-priceb-title-v2",
            }
        )
        invalid_claim = dict(claim)
        invalid_claim.update(
            {
                "detail_title": "Product",
                "detail_title_sha256": hashlib.sha256(b"Product").hexdigest(),
            }
        )
        with self.assertRaises(UserError):
            Item.apply_cost_recovery_plan(
                [invalid_claim],
                "cost-apply-worker",
                "sparex-dealer-cost-recovery",
                "Reject placeholder detail title evidence",
            )
        applied = Item.apply_cost_recovery_plan(
            [claim],
            "cost-apply-worker",
            "sparex-dealer-cost-recovery",
            "Test exact dealer cost recovery",
        )
        self.assertEqual(supplierinfo.price, 14.99)
        self.assertEqual(product.standard_price, 14.99)
        self.assertEqual(product.list_price, 24.18)
        self.assertFalse(product.southern_quote_only)
        self.assertEqual(product.southern_price_basis, "cost_plus")
        self.assertEqual(product.southern_cost_plus_margin_percent, 35.0)
        self.assertEqual(item.listing_title, "Verified Hydraulic Part")
        self.assertEqual(item.detail_title_sha256, hashlib.sha256(b"Verified Hydraulic Part").hexdigest())
        self.assertEqual(item.detail_title_page_sha256, "d" * 64)
        item.write(
            {
                "listing_title": False,
                "detail_title_sha256": False,
                "detail_title_page_sha256": False,
                "detail_title_parser_version": False,
                "detail_title_recovered_at": False,
                "detail_page_artifact_uri": False,
                "detail_page_artifact_sha256": False,
            }
        )
        product.write(
            {
                "description_ecommerce": (
                    "Internal catalog record. Not published to the website until pricing, description, "
                    "and product media are reviewed."
                ),
                "website_description": False,
                "description_sale": False,
            }
        )
        item._refresh_readiness()
        self.assertTrue(item.has_positive_supplier_cost)
        self.assertEqual(item.primary_blocker, "missing_customer_description")
        self.assertEqual(item.cost_recovery_state, "queued")
        item.write(
            {
                "listing_title": applied[0]["detail_title_applied"],
                "detail_title_sha256": applied[0]["detail_title_sha256_applied"],
                "detail_title_page_sha256": applied[0]["detail_title_page_sha256_applied"],
                "detail_title_parser_version": "sparex-exact-priceb-title-v2",
                "detail_title_recovered_at": fields.Datetime.now(),
                "detail_page_artifact_uri": applied[0]["detail_page_artifact_uri_applied"],
                "detail_page_artifact_sha256": applied[0]["detail_page_artifact_sha256_applied"],
            }
        )
        product.description_ecommerce = "Customer-ready dealer-cost description."
        item._refresh_readiness()
        product.website_published = True
        item._refresh_readiness()
        self.assertTrue(product.website_published)
        self.assertEqual(item.primary_blocker, "already_published")
        self.assertEqual(item.cost_evidence_sha256, "d" * 64)
        Item.rollback_cost_recovery(applied, "Test scoped rollback")
        self.assertEqual(supplierinfo.price, 0.0)
        self.assertEqual(product.standard_price, 0.0)
        self.assertEqual(product.list_price, 0.0)
        self.assertTrue(product.southern_quote_only)
        self.assertEqual(product.southern_price_basis, "none")
        self.assertFalse(item.listing_title)
        self.assertFalse(item.detail_title_sha256)
        self.assertFalse(item.detail_page_artifact_uri)
        self.assertEqual(item.cost_recovery_state, "manual_review")
