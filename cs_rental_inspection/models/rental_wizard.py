# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import models


class RentalOrderWizard(models.TransientModel):
    _inherit = 'rental.order.wizard'

    def apply(self):
        result = super().apply()
        if self.status == 'pickup':
            self.order_id._auto_create_serialised_pickup_inspections()
        return result
