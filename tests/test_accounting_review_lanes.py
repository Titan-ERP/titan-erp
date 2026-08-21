import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "southern_accounting_guardrails" / "accounting_review.py"
SPEC = importlib.util.spec_from_file_location("accounting_review", MODULE_PATH)
accounting_review = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(accounting_review)

MIGRATION_PATH = (
    ROOT
    / "southern_accounting_guardrails"
    / "migrations"
    / "19.0.1.11.0"
    / "post-migrate.py"
)
MIGRATION_SPEC = importlib.util.spec_from_file_location(
    "accounting_review_migration", MIGRATION_PATH
)
accounting_review_migration = importlib.util.module_from_spec(MIGRATION_SPEC)
MIGRATION_SPEC.loader.exec_module(accounting_review_migration)


class RecordingCursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params=None):
        self.calls.append((query, params))


class InvoiceReviewLaneTest(unittest.TestCase):
    def test_native_odoo_invoice_is_not_required(self):
        self.assertEqual(
            accounting_review.invoice_default_review_status("odoo"),
            "not_required",
        )
        self.assertEqual(
            accounting_review.classify_invoice_review("odoo", "not_required"),
            ("not_required", "Native Odoo invoice with no legacy source to verify."),
        )

    def test_shop_boss_source_enters_source_review(self):
        self.assertEqual(
            accounting_review.invoice_default_review_status("shop_boss"),
            "needs_review",
        )
        lane, details = accounting_review.classify_invoice_review(
            "shop_boss",
            "needs_review",
            has_shop_boss_reference=True,
            shop_boss_number="1044",
        )
        self.assertEqual(lane, "source_review")
        self.assertIn("Shop Boss", details)

    def test_extracted_reference_without_source_flag_still_needs_review(self):
        lane, details = accounting_review.classify_invoice_review(
            "odoo",
            "not_required",
            has_shop_boss_reference=True,
        )
        self.assertEqual(lane, "source_review")
        self.assertIn("verification", details)

    def test_verified_and_exception_win(self):
        self.assertEqual(
            accounting_review.classify_invoice_review(
                "shop_boss",
                "verified",
                shop_boss_verified=True,
            )[0],
            "verified",
        )
        self.assertEqual(
            accounting_review.classify_invoice_review("odoo", "exception")[0],
            "exception",
        )


class BankReviewLaneTest(unittest.TestCase):
    def test_direct_revenue_settlement_is_blocked(self):
        lane, details = accounting_review.classify_bank_review(
            "needs_review",
            is_merchant_settlement=True,
            settlement_direct_revenue=True,
        )
        self.assertEqual(lane, "blocked")
        self.assertIn("revenue", details)

    def test_unmatched_merchant_settlement_is_its_own_lane(self):
        lane, details = accounting_review.classify_bank_review(
            "needs_review",
            is_merchant_settlement=True,
            is_reconciled=False,
        )
        self.assertEqual(lane, "merchant")
        self.assertIn("clearing", details)

    def test_payroll_and_check_payee_are_separate(self):
        self.assertEqual(
            accounting_review.classify_bank_review(
                "needs_review",
                review_bucket="payroll",
                is_reconciled=False,
            )[0],
            "payroll",
        )
        self.assertEqual(
            accounting_review.classify_bank_review(
                "needs_review",
                is_generic_check=True,
                missing_partner=True,
            )[0],
            "check_payee",
        )

    def test_reviewed_lines_leave_the_open_queue(self):
        self.assertEqual(
            accounting_review.classify_bank_review("reviewed")[0],
            "reviewed",
        )


class ProductAccountingLaneTest(unittest.TestCase):
    def test_missing_bucket_is_not_ok(self):
        lane, details = accounting_review.classify_product_accounting_review(
            True,
            False,
            "needs_review",
            "ok",
            require_product_bucket=True,
        )
        self.assertEqual(lane, "missing_bucket")
        self.assertIn("revenue bucket", details)

    def test_policy_can_disable_bucket_requirement(self):
        self.assertEqual(
            accounting_review.classify_product_accounting_review(
                True,
                False,
                "ok",
                "ok",
                require_product_bucket=False,
            )[0],
            "ok",
        )

    def test_income_and_cost_combine(self):
        lane, details = accounting_review.classify_product_accounting_review(
            True,
            "parts",
            "needs_review",
            "needs_review",
            expected_income_name="410000 Parts Revenue",
            expected_expense_name="510000 Parts COGS",
        )
        self.assertEqual(lane, "both")
        self.assertIn("410000", details)
        self.assertIn("510000", details)

    def test_unsaleable_products_are_skipped(self):
        self.assertEqual(
            accounting_review.classify_product_accounting_review(
                False,
                False,
                "needs_review",
                "needs_review",
            ),
            ("ok", False),
        )


class RevenueLineReviewTest(unittest.TestCase):
    def test_freight_posted_to_parts_is_needs_review(self):
        review, details = accounting_review.classify_revenue_line_review(
            True,
            "freight",
            "410000",
            current_account_name="410000 Parts Revenue",
        )
        self.assertEqual(review, "needs_review")
        self.assertIn("410000", details)
        self.assertIn("Freight", details)

    def test_fees_posted_to_service_is_needs_review(self):
        review, details = accounting_review.classify_revenue_line_review(
            True,
            "fees",
            "420000",
        )
        self.assertEqual(review, "needs_review")
        self.assertIn("Fees", details)

    def test_parts_on_parts_account_is_ok(self):
        self.assertEqual(
            accounting_review.classify_revenue_line_review(True, "parts", "410000")[0],
            "ok",
        )

    def test_expected_account_mismatch_wins(self):
        review, details = accounting_review.classify_revenue_line_review(
            True,
            "freight",
            "410000",
            expected_account_name="Shipping / Freight Revenue",
            current_account_name="Parts Revenue",
            expected_account_mismatch=True,
        )
        self.assertEqual(review, "needs_review")
        self.assertIn("Shipping / Freight Revenue", details)

    def test_freight_product_on_parts_income_needs_review(self):
        self.assertTrue(
            accounting_review.product_income_needs_review(
                True,
                "freight",
                "410000",
            )
        )
        self.assertFalse(
            accounting_review.product_income_needs_review(
                True,
                "parts",
                "410000",
            )
        )


class InvoiceReviewMigrationTest(unittest.TestCase):
    def test_migration_clears_native_invoice_status_and_lane(self):
        cursor = RecordingCursor()
        accounting_review_migration.migrate(cursor, "19.0.1.10.0")
        self.assertEqual(len(cursor.calls), 1)
        query, params = cursor.calls[0]
        self.assertIn("southern_review_status = 'not_required'", query)
        self.assertIn("southern_review_lane = 'not_required'", query)
        self.assertIn("southern_review_details", query)
        self.assertIn("shop_boss", query)
        self.assertIn("out_invoice", query)
        self.assertEqual(params, (accounting_review.NATIVE_INVOICE_REVIEW_DETAILS,))
        self.assertEqual(
            accounting_review_migration.NATIVE_INVOICE_REVIEW_DETAILS,
            accounting_review.NATIVE_INVOICE_REVIEW_DETAILS,
        )

    def test_bank_coding_cron_is_limited_to_southern_equipment(self):
        source = (
            ROOT / "southern_accounting_guardrails" / "models" / "bank_coding.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SOUTHERN_COMPANY_NAME", source)
        self.assertIn("cron_prepare_candidates", source)
        self.assertNotIn('search([])', source)

    def test_daily_bank_action_uses_open_work_lanes(self):
        daily = (
            ROOT / "southern_accounting_guardrails" / "models" / "daily_control.py"
        ).read_text(encoding="utf-8")
        self.assertIn("BANK_OPEN_WORK_LANES", daily)
        self.assertIn("bank_open_today_count", daily)
        self.assertIn("view_southern_bank_statement_line_review_form", daily)
        views_daily = (
            ROOT
            / "southern_accounting_guardrails"
            / "views"
            / "daily_control_views.xml"
        ).read_text(encoding="utf-8")
        self.assertIn('name="action_view_bank_review"', views_daily)
        self.assertIn('name="bank_open_today_count" widget="statinfo" string="Bank Work"', views_daily)
        self.assertNotIn('name="bank_line_count" widget="statinfo"', views_daily)
        views = (
            ROOT
            / "southern_accounting_guardrails"
            / "views"
            / "bank_statement_line_views.xml"
        ).read_text(encoding="utf-8")
        self.assertIn("view_southern_bank_statement_line_review_form", views)
        self.assertIn("southern_review_details", views)
        for lane in accounting_review.BANK_OPEN_WORK_LANES:
            self.assertIn(f"'{lane}'", views)

    def test_daily_controls_use_the_same_invoice_work_lanes_as_the_menu(self):
        daily = (
            ROOT
            / "southern_accounting_guardrails"
            / "models"
            / "daily_control.py"
        ).read_text(encoding="utf-8")
        views = (
            ROOT
            / "southern_accounting_guardrails"
            / "views"
            / "account_move_views.xml"
        ).read_text(encoding="utf-8")
        self.assertIn("INVOICE_SOURCE_WORK_LANES", daily)
        for lane in accounting_review.INVOICE_SOURCE_WORK_LANES:
            self.assertIn(f"'{lane}'", views)


if __name__ == "__main__":
    unittest.main()
