# -*- coding: utf-8 -*-
from odoo import api, fields, models


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    include_shop_supply = fields.Boolean(string="Include Shop Supply's")
    shop_supply_amount = fields.Monetary(
        string="Shop Supply's",
        compute='_compute_shop_supply_amount',
        store=True,
        currency_field='currency_id',
        help="Shop Supply's will be 3% of the untaxed total. The max amount will be $200.",
    )

    @api.depends('amount_untaxed', 'include_shop_supply')
    def _compute_shop_supply_amount(self):
        for order in self:
            if order.include_shop_supply:
                order.shop_supply_amount = min(order.amount_untaxed * 0.03, 200.0)
            else:
                order.shop_supply_amount = 0.0

    @api.depends_context('lang')
    @api.depends(
        'order_line.price_subtotal', 'currency_id', 'company_id', 'payment_term_id',
        'include_shop_supply', 'shop_supply_amount',
    )
    def _compute_tax_totals(self):
        super()._compute_tax_totals()
        for order in self:
            if not order.include_shop_supply or not order.shop_supply_amount:
                continue
            tax_totals = order.tax_totals
            if not tax_totals or not tax_totals.get('subtotals'):
                continue
            # Inject as the first tax_group entry so it appears immediately
            # after "Untaxed Amount" in both the form widget and the report.
            tax_totals['subtotals'][0]['tax_groups'].insert(0, {
                'id': 0,
                'involved_tax_ids': [],
                'tax_amount_currency': order.shop_supply_amount,
                'tax_amount': order.shop_supply_amount,
                'base_amount_currency': 0.0,
                'base_amount': 0.0,
                'display_base_amount_currency': False,
                'display_base_amount': False,
                'group_name': "Shop Supply's",
                'group_label': "Shop Supply's",
                'shop_supply_description': "Shop Supply's will be 3% of the untaxed total. The max amount will be $200.",
            })
            order.tax_totals = tax_totals
