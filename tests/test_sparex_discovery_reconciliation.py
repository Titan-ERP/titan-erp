import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SparexDiscoveryReconciliationContractTests(unittest.TestCase):
    def test_worker_runs_five_throttled_pages_and_preserves_exact_run(self):
        source = (ROOT / "scripts" / "sparex_catalog_discovery.py").read_text(encoding="utf-8")
        launcher = (ROOT / "scripts" / "run_sparex_catalog_discovery.sh").read_text(encoding="utf-8")
        self.assertIn('parser.add_argument("--max-pages-per-checkpoint", type=int, default=5)', source)
        self.assertIn("for _index in range(checkpoint_pages):", source)
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


if __name__ == "__main__":
    unittest.main()
