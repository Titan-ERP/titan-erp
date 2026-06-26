# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import models


class RentalOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def write(self, vals):
        result = super().write(vals)
        if 'pickedup_lot_ids' in vals:
            # When the rental module assigns lots on pickup, auto-create
            # serialised pickup inspection drafts for the affected orders.
            orders = self.mapped('order_id').filtered('is_rental_order')
            for order in orders:
                order._auto_create_serialised_pickup_inspections()
        return result
