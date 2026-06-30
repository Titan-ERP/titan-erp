# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import fields, models

class ProductCategory(models.Model):
    _inherit = 'product.category'

    pickup_template_id = fields.Many2one('rental.inspection.template', string='Pickup Inspection Template',
                                      domain=[('type', '=', 'pickup')])
    return_template_id = fields.Many2one('rental.inspection.template', string='Return Inspection Template',
                                      domain=[('type', '=', 'return')])
