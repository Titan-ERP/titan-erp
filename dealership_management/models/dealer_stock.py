# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
from odoo import fields, models, api, _
import logging
_log = logging.getLogger(__name__)

class DealerStock(models.Model):
    _name = 'dealer.stock.line'
    _description = 'Dealer Stock'

    qty = fields.Float(string='Quantity', required=True, default=1)
    product_id = fields.Many2one('product.product', string='Product', required=True)
    application_id = fields.Many2one('dealership.application', required=True)
    order_ids = fields.Many2many('sale.order', string="Order")
    ordered_qty = fields.Float(string='Order Quantity', compute='_compute_ordered_qty')
    delivered_qty = fields.Float(string='Delivered Quantity', compute='_compute_delivered_qty')


    def _compute_ordered_qty(self):
        for record in self:
            qty = 0
            if self.product_id.type != 'service':
                for order in self.order_ids:
                    for picking in order.picking_ids.filtered(lambda x: x.state != 'done'):
                        moves = picking.move_line_ids.filtered(lambda x: x.product_id.id == record.product_id.id)
                        qty += sum(moves.mapped('quantity'))
            record.ordered_qty = qty

    def _compute_delivered_qty(self):
        for record in self:
            if self.product_id.type != 'service':
                qty = 0
                for order in self.order_ids:
                    for picking in order.picking_ids.filtered(lambda x: x.state == 'done'):
                        moves = picking.move_line_ids.filtered(lambda x: x.product_id.id == record.product_id.id)
                        for move in moves:
                            qty += move.quantity_done
            else:
                qty = self.qty
            record.delivered_qty = qty
