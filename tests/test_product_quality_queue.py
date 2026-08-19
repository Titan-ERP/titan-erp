import ast
import importlib.util
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_quality_rules():
    path = ROOT / "southern_parts_intelligence" / "quality_rules.py"
    spec = importlib.util.spec_from_file_location("southern_quality_rules", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProductQualityQueueTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rules = load_quality_rules()

    def classify(self, **overrides):
        facts = {
            "price": 24.99,
            "cost": 10.0,
            "verified_supplier_cost": 10.0,
            "is_sparex": True,
            "published": False,
            "source_url": "https://us.sparex.com/p/s-610",
            "evidence_count": 2,
            "has_website_category": True,
            "has_image": True,
            "description_ready": True,
            "sparex_publication_eligible": True,
            "reference": "S.610",
            "duplicate_count": 1,
        }
        facts.update(overrides)
        return self.rules.classify_product_quality(**facts)

    def types(self, **overrides):
        return [finding.issue_type for finding in self.classify(**overrides)]

    def test_ready_unpublished_sparex_is_release_lane_not_a_defect(self):
        findings = self.classify()
        self.assertEqual([finding.issue_type for finding in findings], ["publication_ready"])
        self.assertEqual(findings[0].work_lane, "release")
        self.assertEqual(findings[0].severity, "1_low")

    def test_published_placeholder_price_is_a_live_blocker(self):
        findings = self.classify(price=1.0, published=True, sparex_publication_eligible=False)
        types = [finding.issue_type for finding in findings]
        self.assertIn("placeholder_price", types)
        placeholder = next(finding for finding in findings if finding.issue_type == "placeholder_price")
        self.assertEqual(placeholder.work_lane, "live_fix")
        self.assertEqual(placeholder.severity, "4_blocker")
        self.assertIn("$1.00", placeholder.details)
        self.assertNotIn("publication_ready", types)

    def test_unpublished_missing_cost_is_enrichment_not_ready(self):
        findings = self.classify(
            verified_supplier_cost=0,
            sparex_publication_eligible=False,
        )
        types = [finding.issue_type for finding in findings]
        self.assertIn("missing_verified_supplier_cost", types)
        self.assertNotIn("publication_ready", types)
        cost = next(
            finding for finding in findings if finding.issue_type == "missing_verified_supplier_cost"
        )
        self.assertEqual(cost.work_lane, "enrich")
        self.assertEqual(cost.severity, "2_medium")

    def test_non_sparex_compares_standard_cost_not_supplier_cost(self):
        types = self.types(
            is_sparex=False,
            verified_supplier_cost=0,
            cost=20.0,
            price=15.0,
            sparex_publication_eligible=False,
        )
        self.assertIn("price_not_above_cost", types)
        self.assertNotIn("missing_verified_supplier_cost", types)

    def test_duplicate_and_missing_evidence_are_classified(self):
        types = self.types(
            source_url="",
            evidence_count=0,
            reference="S.610",
            duplicate_count=3,
            sparex_publication_eligible=False,
            price=1.0,
        )
        self.assertIn("missing_evidence", types)
        self.assertIn("duplicate_reference", types)

    def test_quality_refresh_batches_existing_issues(self):
        source = (ROOT / "southern_parts_intelligence" / "models" / "product_quality.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("existing_by_key", source)
        self.assertIn("classify_product_quality(", source)
        self.assertIn("work_lane", source)
        self.assertIn("limit=500", source)
        self.assertIn("pg_try_advisory_xact_lock", source)
        self.assertIn("issue_type", source)
        self.assertNotIn("for issue_type in self._issue_codes", source)

    def test_quality_views_expose_work_lanes_and_need_work_default(self):
        root = ET.parse(ROOT / "southern_parts_intelligence" / "views" / "product_quality_views.xml").getroot()
        action = root.find("./record[@id='action_southern_product_quality_issues']")
        context = action.find("field[@name='context']").text
        self.assertIn("search_default_needs_work", context)
        self.assertIn("search_default_open", context)
        view_mode = action.find("field[@name='view_mode']").text
        self.assertIn("kanban", view_mode)
        self.assertTrue(root.find("./record[@id='action_southern_product_quality_live_fixes']") is not None)
        self.assertTrue(root.find("./record[@id='action_southern_product_quality_ready']") is not None)
        search_xml = ET.tostring(
            root.find("./record[@id='southern_product_quality_issue_search']"),
            encoding="unicode",
        )
        self.assertIn("needs_work", search_xml)
        self.assertIn("live_fix", search_xml)
        self.assertIn("Ready to Publish", "".join(root.itertext()))

    def test_daily_control_excludes_ready_rows_from_open_issue_count(self):
        source = (ROOT / "southern_operations_control" / "models" / "daily_control.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('("issue_type", "!=", "publication_ready")', source)
        self.assertIn("product_ready_count", source)
        self.assertIn("product_live_fix_count", source)
        self.assertIn('("issue_type", "=", "publication_ready")', source)

    def test_module_versions_were_bumped(self):
        parts = ast.literal_eval(
            (ROOT / "southern_parts_intelligence" / "__manifest__.py").read_text(encoding="utf-8")
        )
        operations = ast.literal_eval(
            (ROOT / "southern_operations_control" / "__manifest__.py").read_text(encoding="utf-8")
        )
        self.assertEqual(parts["version"], "19.0.1.47.0")
        self.assertEqual(operations["version"], "19.0.1.2.0")
        self.assertIn("views/product_quality_views.xml", parts["data"])


if __name__ == "__main__":
    unittest.main()
