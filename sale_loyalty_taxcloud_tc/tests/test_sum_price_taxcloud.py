import itertools

from odoo.addons.account_taxcloud_tc.tests.common import TestAccountTaxcloudCommon

from . import common


def record_powerset(records):
    def flatten_subset(subset):
        if len(list(subset)):
            recordset = subset[0]
            for record in subset[1:]:
                recordset += record
        else:
            recordset = records.browse()
        return recordset

    powerset = list()
    for n in range(len(records) + 1):
        for subset in itertools.combinations(records, n):
            powerset.append(flatten_subset(subset))
    return powerset


class TestSaleCouponPriceTaxCloud(
    common.TestSaleCouponTaxCloudCommon, TestAccountTaxcloudCommon
):
    def test_total(self):
        """Test that the sum of TaxCloud is equal to the order total
        (with applied discounts), for all possible sets of discounts.
        This is the most important coherency issue.
        So we don't test how coupon are applied, just that the result of our
        computations match what is obtained from the lines.
        """
        TaxCloud = self.order._get_TaxCloudRequest("id", "api_key", "v3")

        for applied_discounts in record_powerset(self.all_programs):
            self.order.applied_coupon_ids = applied_discounts.coupon_ids
            with self.mock_taxcloud():
                self.order._update_programs_and_rewards()
            lines = self.order.order_line
            TaxCloud._apply_discount_on_lines(lines)
            sum_taxcloud = sum(
                lines.filtered(lambda x: x.price_taxcloud > 0).mapped(
                    lambda y: (y.price_taxcloud * y.product_uom_qty) + y.price_tax
                )
            )

            self.assertEqual(sum_taxcloud, self.order.amount_total)

    def test_free_product(self):
        # Force pricelist with no discount to avoid price alteration
        no_discount_pricelist = self.env['product.pricelist'].search([
            ('item_ids', '=', False),  # no rules = no discount
        ], limit=1)
        if not no_discount_pricelist:
            no_discount_pricelist = self.env['product.pricelist'].create({
                'name': 'No Discount Pricelist',
                'currency_id': self.env.company.currency_id.id,
            })
        self.order.pricelist_id = no_discount_pricelist

        TaxCloud = self.order._get_TaxCloudRequest("id", "api_key", "v3")
        self._apply_promo_code(
            self.order, self.program_free_product_C.coupon_ids.code
        )

        discount_line = self.order.order_line.filtered("reward_id")
        self.assertEqual(discount_line.price_unit, -10)
        self.assertEqual(discount_line.product_uom_qty, 1)

        lines = self.order.order_line
        TaxCloud._apply_discount_on_lines(lines)

        line_C = lines.filtered(lambda x: x.product_id == self.product_C)
        self.assertEqual(line_C.price_taxcloud, 0.0)

        other_lines = lines.filtered(lambda x: x.price_taxcloud > 0) - line_C
        for line in other_lines:
            self.assertAlmostEqual(line.price_taxcloud, line.price_unit)
