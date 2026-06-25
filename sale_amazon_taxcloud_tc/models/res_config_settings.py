from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    is_dont_compute_tax_for_amazon_orders = fields.Boolean(
        string="Don't Compute tax for Amazon Orders",
        related="company_id.is_dont_compute_tax_for_amazon_orders",
        readonly=False,
    )
