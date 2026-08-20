import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ProductionHardeningTests(unittest.TestCase):
    def source(self, relative):
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_changed_modules_have_release_metadata(self):
        for module in (
            "southern_accounting_guardrails",
            "southern_customer_portal",
            "southern_equipment_brokerage",
            "southern_operations_control",
            "southern_parts_intelligence",
        ):
            manifest = ast.literal_eval(self.source(f"{module}/__manifest__.py"))
            self.assertTrue(manifest.get("author"), module)
            self.assertTrue(manifest.get("website"), module)
            self.assertEqual(manifest.get("license"), "LGPL-3")

    def test_product_crons_are_bounded_and_overlap_protected(self):
        quality = self.source("southern_parts_intelligence/models/product_quality.py")
        evidence = self.source("southern_parts_intelligence/models/evidence_queue.py")
        catalog = self.source("southern_parts_intelligence/models/catalog_sync.py")
        self.assertIn("limit=500", quality)
        self.assertIn("pg_try_advisory_xact_lock", quality)
        self.assertIn("pg_try_advisory_xact_lock", evidence)
        self.assertIn("internal_cron_enabled", catalog)
        self.assertIn("batch_size = max(min", catalog)
        self.assertIn("MAX_CONSECUTIVE_RELEASES = 7", catalog)
        self.assertIn("releases_since_discovery < MAX_CONSECUTIVE_RELEASES", catalog)

    def test_partner_pricing_application_is_off_by_default(self):
        portal = self.source("southern_customer_portal/controllers/portal.py")
        template = self.source(
            "southern_customer_portal/views/customer_portal_templates.xml"
        )
        self.assertIn("partner_application_enabled\", \"false\"", portal)
        self.assertIn("company_fax", template)
        self.assertIn("timedelta(minutes=10)", portal)

    def test_company_two_is_not_hardcoded(self):
        for relative in (
            "southern_equipment_brokerage/models/comp_analysis.py",
            "southern_customer_portal/models/sale_order.py",
            "southern_operations_control/models/daily_control.py",
        ):
            source = self.source(relative)
            self.assertNotIn("TARGET_COMPANY_ID", source)
            self.assertNotIn("company_id.id != 2", source)

    def test_contact_matching_uses_odoo_19_domain_api(self):
        source = self.source("southern_operations_control/models/contact_import.py")
        self.assertIn("from odoo.fields import Domain", source)
        self.assertNotIn("from odoo.osv", source)

    def test_product_bucket_policy_is_enforced_in_review(self):
        source = self.source("southern_accounting_guardrails/models/product_template.py")
        self.assertIn("require_product_bucket", source)
        self.assertIn("southern_accounting_review_lane", source)
        rules = self.source("southern_accounting_guardrails/accounting_review.py")
        self.assertIn("def classify_invoice_review", rules)
        self.assertIn("def classify_bank_review", rules)
        self.assertIn("def classify_product_accounting_review", rules)
        self.assertIn("def classify_revenue_line_review", rules)
        policy = self.source("southern_accounting_guardrails/models/accounting_policy.py")
        self.assertIn("Transaction Processing Fee Income", policy)

    def test_apply_run_requires_idempotency_and_verified_artifact(self):
        source = self.source(
            "southern_parts_intelligence/models/automation_control.py"
        )
        self.assertIn("idempotency_key", source)
        self.assertIn("artifact_sha256", source)
        self.assertIn("artifact_schema_version", source)
        self.assertIn("artifact_archived", source)
        self.assertIn("pg_try_advisory_xact_lock", source)


if __name__ == "__main__":
    unittest.main()
