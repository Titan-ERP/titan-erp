# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import models


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    def button_confirm(self):
        result = super().button_confirm()
        self._sync_rental_lots_to_receipt_moves()
        return result

    def _sync_rental_lots_to_receipt_moves(self):
        for order in self:
            for line in order.order_line:
                # sale_line_id is provided by the sale_purchase module
                if 'sale_line_id' not in line._fields:
                    continue
                sale_line = line.sale_line_id
                if not sale_line:
                    continue
                if not getattr(sale_line.order_id, 'is_rental_order', False):
                    continue
                if 'reserved_lot_ids' not in sale_line._fields:
                    continue
                lots = sale_line.reserved_lot_ids
                if not lots:
                    continue
                lot = lots.sorted('id')[:1]
                for move in line.move_ids.filtered(lambda m: m.state not in ('done', 'cancel')):
                    if lot not in move.lot_ids:
                        move.lot_ids = [(4, lot.id)]
