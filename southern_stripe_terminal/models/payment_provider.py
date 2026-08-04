from odoo import fields, models


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    stripe_terminal_webhook_secret = fields.Char(
        string="Stripe Terminal Webhook Signing Secret",
        copy=False,
        groups="base.group_system",
        help="Signing secret for the dedicated Southern Stripe Terminal webhook endpoint.",
    )
