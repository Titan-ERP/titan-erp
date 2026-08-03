import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "southern_accounting_guardrails" / "models" / "bank_review_logic.py"
SPEC = importlib.util.spec_from_file_location("bank_review_logic", MODULE_PATH)
bank_review_logic = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bank_review_logic)


class BankReviewLogicTest(unittest.TestCase):
    def test_positive_supplier_refund_is_vendor(self):
        self.assertEqual(
            bank_review_logic.classify_review_bucket(
                "Vendor refund",
                125.0,
                has_partner=True,
                supplier_rank=1,
            ),
            "vendor",
        )

    def test_positive_customer_deposit_remains_legacy_payment(self):
        self.assertEqual(
            bank_review_logic.classify_review_bucket("Customer deposit", 125.0),
            "shop_boss_payment",
        )

    def test_payroll_direct_expense_is_risk(self):
        self.assertTrue(
            bank_review_logic.payroll_direct_expense_risk(
                "INTUIT PAYROLL",
                -1250.0,
                {"asset_cash", "expense_direct_cost"},
            )
        )
        self.assertFalse(
            bank_review_logic.payroll_direct_expense_risk(
                "INTUIT PAYROLL",
                -1250.0,
                {"asset_cash", "liability_current"},
            )
        )

    def test_merchant_settlement_direct_revenue_is_risk(self):
        self.assertTrue(
            bank_review_logic.settlement_direct_revenue_risk(
                "BANKCARD NET SETTLE",
                900.0,
                {"asset_cash", "income"},
            )
        )
        self.assertFalse(
            bank_review_logic.settlement_direct_revenue_risk(
                "BANKCARD NET SETTLE",
                900.0,
                {"asset_cash", "asset_current", "expense"},
            )
        )


if __name__ == "__main__":
    unittest.main()
