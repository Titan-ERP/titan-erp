import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SparexDiscoveryReconciliationContractTests(unittest.TestCase):
    def test_worker_runs_five_throttled_pages_and_preserves_exact_run(self):
        source = (ROOT / "scripts" / "sparex_catalog_discovery.py").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts" / "run_sparex_catalog_discovery.sh").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--max-pages-per-checkpoint", type=int, default=5)', source)
        self.assertIn("MAX_TOTAL_PAGES = 10_000_000", source)
        self.assertIn("for _index in range(checkpoint_pages):", source)
        self.assertIn("backfill_legacy_page_urls", source)
        self.assertIn('"queue_due_discovery_page_repairs"', source)
        self.assertIn('"configure_discovery_checkpoint"', source)
        self.assertIn('"prepare_reconciliation_run"', source)
        self.assertIn("scripts.odoo_product_dispatch_worker", launcher)
        dispatcher = (ROOT / "scripts" / "odoo_product_dispatch_worker.py").read_text(encoding="utf-8")
        self.assertIn("MAX_PORTAL_LIMIT = 5", dispatcher)
        self.assertIn('min(int(request.get("limit") or MAX_PORTAL_LIMIT), MAX_PORTAL_LIMIT)', dispatcher)
        self.assertIn("sparex-full-catalog-inventory-v3", dispatcher)
        self.assertIn('"selection_scope": "current_catalog_backlog"', source)
        self.assertNotIn('item_ids=recorded.get("item_ids") or []', source)

    def test_odoo_model_has_reconciliation_recovery_and_source_link_contracts(self):
        source = (ROOT / "southern_parts_intelligence" / "models" / "sparex_discovery.py").read_text(
            encoding="utf-8"
        )
        for contract in (
            "prepare_reconciliation_run",
            "_complete_reconciliation",
            "reconciliation_state",
            "stale_not_seen",
            "consecutive_failure_count",
            "prepare_source_link_plan",
            '("source_enrichment_candidate", "=", True)',
            "apply_source_link_plan",
            "rollback_source_links",
            "claim_cost_recovery_batch",
            "record_cost_recovery_result",
            "apply_cost_recovery_plan",
            "rollback_cost_recovery",
            "cost_recovery_next_at",
            "review_required",
            "prepare_product_creation_plan",
            "apply_product_creation_plan",
            "rollback_created_products",
            "page_driven_creation_enabled",
            'updates["max_pages_total"] = MAX_DISCOVERY_TOTAL_PAGES',
            "queue_discovery_page_repairs",
            "_ensure_normalized_frontier",
            "southern.sparex.discovery.url",
            "repair_queued_url_count",
        ):
            self.assertIn(contract, source)

    def test_dashboard_and_missing_product_views_are_valid_xml(self):
        path = ROOT / "southern_parts_intelligence" / "views" / "sparex_discovery_views.xml"
        ET.parse(path)
        source = path.read_text(encoding="utf-8")
        self.assertIn("Sparex Discovery Dashboard", source)
        self.assertIn("Missing Sparex Products", source)
        self.assertIn("Sparex Dealer Cost Recovery", source)
        self.assertIn("cost_recovery_queued", source)
        self.assertIn('widget="progressbar"', source)

    def test_publication_requires_current_discovery_evidence(self):
        source = (ROOT / "southern_parts_intelligence" / "models" / "catalog_agents.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_current_discovery_item", source)
        self.assertIn("missing_current_discovery_evidence", source)

    def test_listing_inventory_uses_bounded_bulk_database_matching(self):
        discovery = (ROOT / "southern_parts_intelligence" / "models" / "sparex_discovery.py").read_text(
            encoding="utf-8"
        )
        catalog = (ROOT / "southern_parts_intelligence" / "models" / "vendor_catalog.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("normalized_observations", discovery)
        self.assertIn("supplier_by_product_id", discovery)
        self.assertIn("tracking_disable=True", discovery)
        self.assertIn("def _match_products", catalog)
        self.assertIn("product_matches = self._match_products", catalog)
        self.assertIn("10_000_000", discovery)
        self.assertNotIn("MAX_DISCOVERY_FRONTIER_URLS", discovery)

    def test_url_queue_checkpoint_cost_does_not_scale_with_catalog_size(self):
        discovery = (ROOT / "southern_parts_intelligence" / "models" / "sparex_discovery.py").read_text(
            encoding="utf-8"
        )
        checkpoint = discovery.split("def record_discovery_page", 1)[1].split(
            "def record_discovery_failure", 1
        )[0]
        repair_queue = discovery.split("def queue_discovery_page_repairs", 1)[1].split(
            "def prepare_legacy_page_url_backfill", 1
        )[0]
        reconciliation = discovery.split("def prepare_reconciliation_run", 1)[1].split(
            "def _ensure_normalized_frontier", 1
        )[0]
        release_status = discovery.split("def continuous_release_status", 1)[1].split(
            "def claim_cost_recovery_batch", 1
        )[0]
        item_init = discovery.split("def init(self):", 1)[1].split("def _compute_blocker_summary", 1)[0]
        refresh_selection = release_status.split("refresh_items = self.search", 1)[1].split(
            "refresh_items._refresh_readiness", 1
        )[0]
        self.assertNotIn("search_count(", checkpoint)
        self.assertNotIn("search_count(", repair_queue)
        self.assertNotIn("Item.search(", reconciliation)
        self.assertIn("last_seen_run_id IS DISTINCT FROM", reconciliation)
        self.assertIn("limit=500", release_status)
        self.assertIn('order="readiness_refreshed_at, id"', release_status)
        self.assertNotIn('(\"currently_published\", \"=\", False)', refresh_selection)
        self.assertNotIn("currently_published IS FALSE", release_status)
        self.assertNotIn("currently_published IS FALSE", item_init)
        self.assertIn("JOIN product_template product", release_status)
        self.assertIn('flush_model(["is_published"])', release_status)
        self.assertIn("product.is_published IS FALSE", release_status)
        self.assertNotIn("product.website_published", release_status)
        self.assertNotIn("tracked_product_ids", release_status)
        self.assertIn("NOT EXISTS", release_status)
        self.assertIn("southern_sparex_discovery_url_frontier_idx", discovery)
        self.assertIn("southern_sparex_discovery_url_repair_idx", discovery)
        self.assertIn("southern_sparex_discovery_item_release_idx", discovery)
        self.assertIn("southern_sparex_discovery_item_refresh_idx", discovery)


if __name__ == "__main__":
    unittest.main()
