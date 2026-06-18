# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import fields, models


class RentalOrderLine(models.Model):
    _inherit = 'sale.order.line'

    reserved_lot_ids = fields.Many2many(
        domain="[('product_id', '=', product_id), ('quant_ids.location_id.complete_name', 'ilike', 'RENTL/Stock')]",
    )

    def write(self, vals):
        result = super().write(vals)
        if 'pickedup_lot_ids' in vals or 'reserved_lot_ids' in vals:
            # Sync serial numbers to inspections whenever lots change.
            # pickedup_lot_ids: actual pickup — takes priority.
            # reserved_lot_ids: planning intent — used until pickup happens.
            orders = self.mapped('order_id').filtered('is_rental_order')
            for order in orders:
                order._auto_create_serialised_pickup_inspections()
        return result
