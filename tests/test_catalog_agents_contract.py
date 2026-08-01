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

    def test_five_named_agents_are_seeded(self):
        root = ET.parse(
            ROOT / "southern_parts_intelligence" / "data" / "catalog_agent_defaults.xml"
        ).getroot()
        names = {
            field.text
            for field in root.findall(".//record[@model='southern.catalog.agent']/field[@name='name']")
        }
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

    def test_odoo_agent_model_never_writes_products_or_standard_cost(self):
        source = (
            ROOT / "southern_parts_intelligence" / "models" / "catalog_agents.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("standard_price", source)
        self.assertNotIn('product.write(', source)
        self.assertNotIn('"website_published":', source)
        self.assertIn("MAX_AGENT_BATCH = 5", source)
        self.assertIn("throttle_seconds < 3.0", source)
        self.assertIn("exact_sparex_url(product.southern_source_url, normalized)", source)

    def test_external_worker_is_read_only_by_default(self):
        source = (
            ROOT / "scripts" / "sparex_catalog_agents" / "worker.py"
        ).read_text(encoding="utf-8")
        self.assertIn("ApplyGate", source)
        self.assertIn('WORKFLOW = "catalog-agent-results"', source)
        self.assertIn("MAX_BATCH = 5", source)
        self.assertIn("if args.apply and not args.run_ai", source)

    def test_agent_sdk_dependency_is_optional(self):
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn('agents = ["openai-agents>=0.19.2,<0.20"]', pyproject)
        self.assertNotIn("openai-agents", requirements)


if __name__ == "__main__":
    unittest.main()
