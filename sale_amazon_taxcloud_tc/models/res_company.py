from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    is_dont_compute_tax_for_amazon_orders = fields.Boolean(
        string="Don't Compute tax for Amazon Orders"
    )
