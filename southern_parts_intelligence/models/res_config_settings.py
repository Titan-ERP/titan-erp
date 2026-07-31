from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    sparex_dealer_login_url = fields.Char(
        string="Sparex Dealer Login URL",
        config_parameter="southern_parts_intelligence.sparex_dealer_login_url",
        default="https://us.sparex.com/customer/account/",
    )
    sparex_dealer_products_url = fields.Char(
        string="Sparex Dealer Products URL",
        config_parameter="southern_parts_intelligence.sparex_dealer_products_url",
        default="https://us.sparex.com/",
    )
    sparex_dealer_username = fields.Char(
        string="Sparex Dealer Username",
        config_parameter="southern_parts_intelligence.sparex_dealer_username",
    )
    sparex_dealer_password = fields.Char(
        string="Sparex Dealer Password",
        config_parameter="southern_parts_intelligence.sparex_dealer_password",
        groups="base.group_system",
    )
