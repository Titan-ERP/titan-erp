from odoo import fields, models


class SouthernStripeTerminalEvent(models.Model):
    _name = "southern.stripe.terminal.event"
    _description = "Stripe Terminal Webhook Event"
    _order = "received_at desc, id desc"

    stripe_event_id = fields.Char(required=True, index=True, readonly=True)
    event_type = fields.Char(required=True, index=True, readonly=True)
    payment_id = fields.Many2one("southern.stripe.terminal.payment", readonly=True, ondelete="set null")
    received_at = fields.Datetime(default=fields.Datetime.now, required=True, readonly=True)
    processed = fields.Boolean(default=False, readonly=True)
    processing_note = fields.Char(readonly=True)

    _stripe_event_id_unique = models.Constraint(
        "UNIQUE(stripe_event_id)",
        "This Stripe webhook event was already received.",
    )
