import importlib.util
import unittest
from pathlib import Path
from types import SimpleNamespace

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "southern_accounting_guardrails"
    / "models"
    / "merchant_guard.py"
)
SPEC = importlib.util.spec_from_file_location("merchant_guard", MODULE_PATH)
merchant_guard = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(merchant_guard)


class MerchantSettlementGuardTest(unittest.TestCase):
    def bank_line(self, label, amount=100.0):
        return SimpleNamespace(
            payment_ref=label,
            ref=False,
            partner_name=False,
            amount=amount,
        )

    def account(self, name, account_type="asset_current"):
        return SimpleNamespace(name=name, account_type=account_type)

    def test_blocks_merchant_settlement_to_revenue(self):
        self.assertTrue(
            merchant_guard.is_unsafe_merchant_target(
                self.bank_line("MERCHANT SERVICE/NET SETTLE"),
                self.account("Parts Revenue", "income"),
            )
        )

    def test_blocks_named_control_accounts(self):
        for name in ("Accounts Receivable", "Sales Tax Payable", "Bank Suspense Account"):
            with self.subTest(name=name):
                self.assertTrue(
                    merchant_guard.is_unsafe_merchant_target(
                        self.bank_line("Merchant Deposit/Credit"),
                        self.account(name),
                    )
                )

    def test_allows_outstanding_receipts(self):
        self.assertFalse(
            merchant_guard.is_unsafe_merchant_target(
                self.bank_line("BANKCARD MTOT DEP"),
                self.account("Outstanding Receipts"),
            )
        )

    def test_does_not_block_money_out_or_nonmerchant_receipts(self):
        self.assertFalse(
            merchant_guard.is_unsafe_merchant_target(
                self.bank_line("MERCHANT SERVICE/NET SETTLE", amount=-50.0),
                self.account("Software Subscriptions", "expense"),
            )
        )
        self.assertFalse(
            merchant_guard.is_unsafe_merchant_target(
                self.bank_line("Customer check deposit"),
                self.account("Parts Revenue", "income"),
            )
        )


if __name__ == "__main__":
    unittest.main()
