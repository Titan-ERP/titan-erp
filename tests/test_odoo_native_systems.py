import ast
import csv
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OdooNativeSystemsTests(unittest.TestCase):
    def test_manifests_parse_and_operations_dependencies_are_explicit(self):
        modules = (
            "southern_parts_intelligence",
            "southern_accounting_guardrails",
            "southern_equipment_brokerage",
            "southern_operations_control",
        )
        manifests = {}
        for module in modules:
            manifest = ast.literal_eval(
                (ROOT / module / "__manifest__.py").read_text(encoding="utf-8")
            )
            self.assertTrue(manifest["installable"])
            manifests[module] = manifest
        operations_dependencies = set(manifests["southern_operations_control"]["depends"])
        self.assertTrue(
            {
                "southern_parts_intelligence",
                "southern_accounting_guardrails",
                "southern_equipment_brokerage",
            }.issubset(operations_dependencies)
        )

    def test_all_custom_addon_xml_parses(self):
        files = sorted(ROOT.glob("southern_*/**/*.xml"))
        self.assertGreaterEqual(len(files), 30)
        for path in files:
            ET.parse(path)

    def test_odoo_19_search_views_do_not_use_legacy_expand_groups(self):
        for path in ROOT.glob("southern_*/**/*.xml"):
            root = ET.parse(path).getroot()
            for search in root.findall(".//search"):
                legacy_groups = search.findall("./group[@expand]")
                self.assertFalse(legacy_groups, path)

    def test_access_files_have_complete_permissions(self):
        for path in ROOT.glob("southern_*/security/ir.model.access.csv"):
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertTrue(rows, path)
            for row in rows:
                for permission in (
                    "perm_read",
                    "perm_write",
                    "perm_create",
                    "perm_unlink",
                ):
                    self.assertIn(row[permission], {"0", "1"}, (path, row))

    def test_product_automation_floor_is_two_gb(self):
        source = (
            ROOT
            / "southern_parts_intelligence"
            / "models"
            / "automation_control.py"
        ).read_text(encoding="utf-8")
        self.assertIn("minimum_free_gb = fields.Float(", source)
        self.assertIn("default=2.0", source)
        self.assertIn('_name = "southern.parts.catalog.sync"', source)

    def test_stored_compute_dependencies_do_not_require_studio_fields(self):
        source = (
            ROOT
            / "southern_parts_intelligence"
            / "models"
            / "product_template.py"
        ).read_text(encoding="utf-8")
        dependency_blocks = source.split("@api.depends(")[1:]
        for block in dependency_blocks:
            declaration = block.split(")", 1)[0]
            self.assertNotIn("x_studio_", declaration)

    def test_company_dependent_partner_price_uses_supported_odoo_type(self):
        source = (
            ROOT
            / "southern_parts_intelligence"
            / "models"
            / "product_template.py"
        ).read_text(encoding="utf-8")
        self.assertIn("southern_partner_price = fields.Float(", source)
        self.assertNotIn("southern_partner_price = fields.Monetary(", source)

    def test_shop_boss_is_not_an_operations_control_dependency(self):
        manifest = ast.literal_eval(
            (ROOT / "southern_operations_control" / "__manifest__.py").read_text(
                encoding="utf-8"
            )
        )
        self.assertNotIn("shop_boss", " ".join(manifest["depends"]).casefold())

    def test_sparex_sourcing_queue_never_writes_standard_cost(self):
        source = (
            ROOT
            / "southern_parts_intelligence"
            / "models"
            / "sparex_sourcing.py"
        ).read_text(encoding="utf-8")
        self.assertIn('_name = "southern.sparex.sourcing.queue"', source)
        self.assertIn('"product.supplierinfo"', source)
        self.assertNotIn('"standard_price":', source)
        self.assertIn("publication_eligible", source)
        self.assertIn("evidence_sha256", source)
        self.assertIn("next_attempt_at", source)

    def test_sparex_pipeline_requires_explicit_artifact_hashes(self):
        source = (ROOT / "scripts" / "sparex_sourcing_pipeline.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("--input-sha256", source)
        self.assertNotIn("latest(", source)
        self.assertNotIn("standard_price", source)


if __name__ == "__main__":
    unittest.main()
