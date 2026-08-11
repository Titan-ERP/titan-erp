import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CatalogAgentsContractTests(unittest.TestCase):
    def test_manifest_installs_agent_defaults_and_views(self):
        manifest = ast.literal_eval(
            (ROOT / "southern_parts_intelligence" / "__manifest__.py").read_text(encoding="utf-8")
        )
        self.assertIn("data/catalog_agent_defaults.xml", manifest["data"])
        self.assertIn("views/catalog_agent_views.xml", manifest["data"])
        self.assertIn("views/sparex_discovery_views.xml", manifest["data"])
        self.assertIn("views/vendor_catalog_views.xml", manifest["data"])

    def test_discovery_queue_uses_gated_odoo_draft_creation(self):
        model_source = (ROOT / "southern_parts_intelligence" / "models" / "sparex_discovery.py").read_text(
            encoding="utf-8"
        )
        worker_source = (ROOT / "scripts" / "sparex_catalog_discovery.py").read_text(encoding="utf-8")
        self.assertIn('_name = "southern.sparex.discovery.run"', model_source)
        self.assertIn('_name = "southern.sparex.discovery.item"', model_source)
        vendor_source = (ROOT / "southern_parts_intelligence" / "models" / "vendor_catalog.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('_name = "southern.vendor.catalog.source"', vendor_source)
        self.assertIn('_name = "southern.vendor.catalog.item"', vendor_source)
        self.assertIn("MAX_CATALOG_UPSERT_BATCH = 2_000", vendor_source)
        self.assertIn("MAX_PROMOTION_BATCH = 200", vendor_source)
        self.assertIn("website_published\": False", vendor_source)
        self.assertIn("MAX_PRODUCT_CREATION_BATCH = 100", model_source)
        self.assertIn("vendor_catalog", model_source)
        migration_source = (
            ROOT
            / "southern_parts_intelligence"
            / "migrations"
            / "19.0.1.22.0"
            / "post-migrate.py"
        ).read_text(encoding="utf-8")
        self.assertIn("southern_sparex_discovery_item", migration_source)
        self.assertIn("ON CONFLICT (source_id, normalized_sku) DO NOTHING", migration_source)
        self.assertIn('(\"not_authorized\", \"Product Creation Not Authorized\")', model_source)
        self.assertIn("prepare_product_creation_plan", model_source)
        self.assertIn("apply_product_creation_plan", model_source)
        self.assertIn("PRODUCT_CREATION_CONFIRMATION", model_source)
        self.assertNotIn('client.call("product.template", "create"', worker_source)

    def test_five_named_agents_are_seeded(self):
        root = ET.parse(ROOT / "southern_parts_intelligence" / "data" / "catalog_agent_defaults.xml").getroot()
        names = {field.text for field in root.findall(".//record[@model='southern.catalog.agent']/field[@name='name']")}
        self.assertEqual(
            names,
            {
                "Catalog Coordinator",
                "Sparex Discovery Agent",
                "Odoo Match Agent",
                "Product Verification Agent",
                "Website Release Agent",
            },
        )

    def test_odoo_release_writes_only_dynamic_publication_flags(self):
        source = (ROOT / "southern_parts_intelligence" / "models" / "catalog_agents.py").read_text(encoding="utf-8")
        self.assertIn("product.write({name: True for name in publication_fields})", source)
        self.assertNotIn('product.write({"standard_price"', source)
        self.assertNotIn('product.write({"list_price"', source)
        self.assertNotIn('product.write({"image_1920"', source)
        self.assertIn("MAX_AGENT_BATCH = 50", source)
        self.assertIn("throttle_seconds < 3.0", source)
        self.assertIn("exact_sparex_url(product.southern_source_url, normalized)", source)
        self.assertIn("if any(bool(product[field_name]) for field_name in publication_fields):", source)
        self.assertIn('["ready", "published", "verified"]', source)

    def test_external_worker_is_read_only_by_default(self):
        source = (ROOT / "scripts" / "sparex_catalog_agents" / "worker.py").read_text(encoding="utf-8")
        self.assertIn("ApplyGate", source)
        self.assertIn('WORKFLOW = "catalog-agent-results"', source)
        self.assertIn("MAX_BATCH = 50", source)
        self.assertIn("require_company_context(config)", source)
        self.assertIn("deterministic_agent_decision", source)
        self.assertIn("requires_ai_review", source)

        orchestrator_source = (
            ROOT / "scripts" / "sparex_catalog_agents" / "orchestrator.py"
        ).read_text(encoding="utf-8")
        self.assertIn("company_id = require_company_context(config)", orchestrator_source)
        self.assertNotIn("config.company_id or False", orchestrator_source)

    def test_failed_ready_root_tasks_can_be_requeued_without_product_writes(self):
        source = (ROOT / "southern_parts_intelligence" / "models" / "catalog_agents.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('task.state in {"blocked", "failed", "cancelled"}', source)
        self.assertIn('"state": "queued"', source)
        self.assertIn('task.publication_state not in {"published", "verified"}', source)

    def test_durable_cost_staging_links_immutable_evidence_to_discovery(self):
        source = (ROOT / "southern_parts_intelligence" / "models" / "sparex_discovery.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("item._link_durable_cost_evidence(catalog_item)", source)
        self.assertIn('"cost_evidence_sha256": evidence_sha', source)
        self.assertIn('"cost_evidence_url_sha256": self.source_url_sha256', source)
        self.assertIn('"cost_recovered_at": catalog_item.dealer_cost_observed_at', source)
        self.assertIn("self._refresh_readiness()", source)
        self.assertIn("def backfill_durable_cost_evidence_links(self, limit=500):", source)

    def test_each_agent_has_one_read_only_function_tool(self):
        source = (ROOT / "scripts" / "sparex_catalog_agents" / "agent.py").read_text(encoding="utf-8")
        for tool_name in (
            "route_catalog_task",
            "verify_sparex_listing",
            "inspect_odoo_match",
            "evaluate_product_readiness",
            "evaluate_release_gate",
        ):
            self.assertIn(f"def {tool_name}(", source)
        self.assertIn('tool_choice="required"', source)
        self.assertIn("parallel_tool_calls=False", source)
        self.assertNotIn("WebSearchTool", source)
        self.assertNotIn("FileSearchTool", source)
        self.assertNotIn("MCPServer", source)

    def test_agent_sdk_dependency_is_optional(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn('agents = ["openai-agents>=0.19.2,<0.20"]', pyproject)
        self.assertNotIn("openai-agents", requirements)


if __name__ == "__main__":
    unittest.main()
