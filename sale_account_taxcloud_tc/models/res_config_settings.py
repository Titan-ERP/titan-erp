from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    is_skip_zero_orders = fields.Boolean(
        string="Skip Zero Orders",
        related="company_id.is_skip_zero_orders",
        readonly=False,
    )
