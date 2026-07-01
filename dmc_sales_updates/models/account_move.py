# -*- coding: utf-8 -*-
from odoo import api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

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
        for move in self:
            if move.include_shop_supply:
                move.shop_supply_amount = min(move.amount_untaxed * 0.03, 200.0)
            else:
                move.shop_supply_amount = 0.0

    @api.depends_context('lang')
    @api.depends(
        'invoice_line_ids.currency_rate',
        'invoice_line_ids.tax_base_amount',
        'invoice_line_ids.tax_line_id',
        'invoice_line_ids.price_total',
        'invoice_line_ids.price_subtotal',
        'invoice_payment_term_id',
        'partner_id',
        'currency_id',
        'include_shop_supply',
        'shop_supply_amount',
    )
    def _compute_tax_totals(self):
        super()._compute_tax_totals()
        for move in self:
            if not move.include_shop_supply or not move.shop_supply_amount:
                continue
            tax_totals = move.tax_totals
            if not tax_totals or not tax_totals.get('subtotals'):
                continue
            tax_totals['subtotals'][0]['tax_groups'].insert(0, {
                'id': 0,
                'involved_tax_ids': [],
                'tax_amount_currency': move.shop_supply_amount,
                'tax_amount': move.shop_supply_amount,
                'base_amount_currency': 0.0,
                'base_amount': 0.0,
                'display_base_amount_currency': False,
                'display_base_amount': False,
                'group_name': "Shop Supply's",
                'group_label': "Shop Supply's",
                'shop_supply_description': "Shop Supply's will be 3% of the untaxed total. The max amount will be $200.",
            })
            move.tax_totals = tax_totals
