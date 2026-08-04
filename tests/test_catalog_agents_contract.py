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

    def test_discovery_queue_uses_gated_odoo_draft_creation(self):
        model_source = (ROOT / "southern_parts_intelligence" / "models" / "sparex_discovery.py").read_text(
            encoding="utf-8"
        )
        worker_source = (ROOT / "scripts" / "sparex_catalog_discovery.py").read_text(encoding="utf-8")
        self.assertIn('_name = "southern.sparex.discovery.run"', model_source)
        self.assertIn('_name = "southern.sparex.discovery.item"', model_source)
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

    def test_external_worker_is_read_only_by_default(self):
        source = (ROOT / "scripts" / "sparex_catalog_agents" / "worker.py").read_text(encoding="utf-8")
        self.assertIn("ApplyGate", source)
        self.assertIn('WORKFLOW = "catalog-agent-results"', source)
        self.assertIn("MAX_BATCH = 50", source)
        self.assertIn("deterministic_agent_decision", source)
        self.assertIn("requires_ai_review", source)

    def test_failed_ready_root_tasks_can_be_requeued_without_product_writes(self):
        source = (ROOT / "southern_parts_intelligence" / "models" / "catalog_agents.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('task.state in {"blocked", "failed", "cancelled"}', source)
        self.assertIn('"state": "queued"', source)
        self.assertIn('task.publication_state not in {"published", "verified"}', source)

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
