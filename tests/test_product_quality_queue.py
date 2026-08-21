import ast
import importlib.util
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_quality_rules():
    path = ROOT / "southern_parts_intelligence" / "quality_rules.py"
    spec = importlib.util.spec_from_file_location("southern_quality_rules", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
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

    def test_ready_row_points_at_approved_release_not_direct_publish(self):
        findings = self.classify()
        self.assertIn("approved Sparex release", findings[0].next_action)
        self.assertIn("does not publish the product", findings[0].next_action)

    def test_refresh_ids_keep_live_products_ahead_of_the_catalog_cursor(self):
        ids = self.rules.merge_quality_refresh_ids([30, 10], [10, 40], [1, 2, 3, 40], limit=5)
        self.assertEqual(ids, [30, 10, 40, 1, 2])

    def test_unseen_published_ids_stay_first_in_the_published_budget(self):
        self.assertEqual(self.rules.QUALITY_PRIORITY_UNSEEN_PUBLISHED_LIMIT, 75)
        self.assertEqual(self.rules.QUALITY_STALE_DAYS, 7)
        ids = self.rules.prioritize_published_refresh_ids(
            [501, 502], [30, 10, 501], published_limit=4
        )
        self.assertEqual(ids, [501, 502, 30, 10])

    def test_dismissed_rows_reopen_only_when_facts_change(self):
        finding = self.classify(price=1.0, published=True, sparex_publication_eligible=False)[0]
        self.assertTrue(
            self.rules.dismissed_should_reopen(
                {
                    "details": finding.details,
                    "severity": finding.severity,
                    "work_lane": finding.work_lane,
                },
                finding,
            )
        )
        self.assertTrue(
            self.rules.dismissed_should_reopen(
                {"details": "Sale price is $0.99", "severity": finding.severity, "work_lane": finding.work_lane},
                finding,
            )
        )
        self.assertTrue(
            self.rules.dismissed_should_reopen(
                {"details": "", "severity": "2_medium", "work_lane": "enrich"},
                finding,
            )
        )
        unpublished = next(
            row
            for row in self.classify(verified_supplier_cost=0, sparex_publication_eligible=False)
            if row.issue_type == "missing_verified_supplier_cost"
        )
        self.assertFalse(
            self.rules.dismissed_should_reopen(
                {"details": "", "severity": unpublished.severity, "work_lane": unpublished.work_lane},
                unpublished,
            )
        )
        self.assertFalse(
            self.rules.dismissed_should_reopen(
                {"accepted_fact_key": self.rules.finding_fact_key(finding)},
                finding,
            )
        )
        unpublished_cost = next(
            row
            for row in self.classify(verified_supplier_cost=0, sparex_publication_eligible=False)
            if row.issue_type == "missing_verified_supplier_cost"
        )
        live_cost = next(
            row
            for row in self.classify(
                verified_supplier_cost=0,
                sparex_publication_eligible=False,
                published=True,
            )
            if row.issue_type == "missing_verified_supplier_cost"
        )
        self.assertTrue(
            self.rules.dismissed_should_reopen(
                {"accepted_fact_key": self.rules.finding_fact_key(unpublished_cost)},
                live_cost,
            )
        )

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
        self.assertIn("dismissed_should_reopen", source)
        self.assertIn("_search_refresh_products", source)
        self.assertIn("_unseen_published_product_ids", source)
        self.assertIn("prioritize_published_refresh_ids", source)
        self.assertIn("QUALITY_PRIORITY_UNSEEN_PUBLISHED_LIMIT", source)
        self.assertIn("action_refresh_selected_products", source)
        self.assertIn("with_company(company)", source)
        self.assertIn("accepted_fact_key=finding_fact_key(finding)", source)
        self.assertIn("row.company_id == self.env.company", source)
        self.assertIn("any(sourcing_rows.mapped(\"publication_eligible\"))", source)
        self.assertIn("_compute_lane_and_severity", source)
        self.assertIn("accepted_fact_key", source)
        self.assertIn('accepted_fact_key": False', source)
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
        self.assertIn("Stale (7 days)", search_xml)
        self.assertIn("Ready to Publish", "".join(root.itertext()))
        self.assertIn("action_open_evidence", ET.tostring(root, encoding="unicode"))

    def test_daily_control_excludes_ready_rows_from_open_issue_count(self):
        source = (ROOT / "southern_operations_control" / "models" / "daily_control.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('("issue_type", "!=", "publication_ready")', source)
        self.assertIn("product_ready_count", source)
        self.assertIn("product_live_fix_count", source)
        self.assertIn('("work_lane", "=", "live_fix")', source)
        self.assertNotIn('("product_published", "=", True)', source)
        self.assertIn('("issue_type", "=", "publication_ready")', source)
        self.assertIn("action_open_live_fixes", source)
        self.assertIn("action_open_ready_products", source)
        self.assertIn("action_open_product_blockers", source)
        self.assertIn("product_stale_count", source)
        self.assertIn("action_open_stale_products", source)
        self.assertIn('("last_detected_at", "<", stale_quality_before)', source)
        self.assertIn('("last_detected_at", "<", cutoff)', source)
        self.assertIn("QUALITY_STALE_DAYS", source)
        views = (ROOT / "southern_operations_control" / "views" / "daily_control_views.xml").read_text(
            encoding="utf-8"
        )
        self.assertIn("action_open_stale_products", views)
        self.assertIn("product_stale_count", views)

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

    def test_hourly_priority_cron_is_bounded_and_active(self):
        root = ET.parse(ROOT / "southern_parts_intelligence" / "data" / "quality_cron.xml").getroot()
        hourly = root.find("./record[@id='ir_cron_southern_product_quality_queue_v3']")
        self.assertIsNotNone(hourly)
        fields = {field.get("name"): (field.text or "").strip() for field in hourly.findall("field")}
        self.assertEqual(fields["interval_type"], "hours")
        self.assertEqual(fields["active"], "True")
        self.assertEqual(fields["code"], "model.cron_refresh_quality_queue()")


if __name__ == "__main__":
    unittest.main()
