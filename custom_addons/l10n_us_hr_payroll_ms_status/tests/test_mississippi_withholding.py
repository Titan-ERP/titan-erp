from decimal import Decimal

from odoo.tests.common import TransactionCase

from ..models.mississippi_withholding import calculate_ms_withholding


class TestMississippiWithholding(TransactionCase):
    def test_single_monthly_under_threshold(self):
        self.assertEqual(
            calculate_ms_withholding(Decimal("1000"), "ms_single", 12),
            Decimal("0.00"),
        )

    def test_single_monthly_over_threshold(self):
        # Annual taxable base: 3,000 * 12 - 2,300 standard deduction
        # - 6,000 exemption = 27,700. Tax is 4% over 10,000 = 708 annually,
        # or 59 monthly after rounding.
        self.assertEqual(
            calculate_ms_withholding(Decimal("3000"), "ms_single", 12),
            Decimal("59"),
        )

    def test_head_of_family_biweekly(self):
        # Annualized wages: 2,000 * 26 = 52,000.
        # Taxable: 52,000 - 3,400 - 9,500 = 39,100.
        # Tax: (39,100 - 10,000) * 4% = 1,164 annually / 26 = 44.77.
        self.assertEqual(
            calculate_ms_withholding(Decimal("2000"), "ms_head_of_family", 26),
            Decimal("45"),
        )
