from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    is_skip_zero_orders = fields.Boolean(string="Skip Zero Orders")
