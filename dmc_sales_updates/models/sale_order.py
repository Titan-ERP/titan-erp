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
    )

    @api.depends('amount_untaxed', 'include_shop_supply')
    def _compute_shop_supply_amount(self):
        for order in self:
            if order.include_shop_supply:
                amount = order.amount_untaxed * 0.03
                order.shop_supply_amount = min(amount, 200.0)
            else:
                order.shop_supply_amount = 0.0
