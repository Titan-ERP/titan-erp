from urllib.parse import urljoin

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SouthernStripeTerminalConfig(models.Model):
    _name = "southern.stripe.terminal.config"
    _description = "Stripe Terminal Reader Configuration"
    _order = "company_id, sequence, name"
    _check_company_auto = True

    name = fields.Char(required=True)
    active = fields.Boolean(default=False)
    sequence = fields.Integer(default=10)
    is_default = fields.Boolean(string="Default Reader")
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    provider_id = fields.Many2one(
        "payment.provider",
        string="Stripe Provider",
        required=True,
        check_company=True,
        domain="[('code', '=', 'stripe'), ('company_id', '=', company_id)]",
    )
    provider_state = fields.Selection(
        related="provider_id.state",
        string="Stripe Provider State",
        readonly=True,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    reader_id = fields.Char(
        string="Stripe Reader ID",
        help="The Stripe Terminal reader identifier (for example, tmr_...).",
    )
    location_id = fields.Char(
        string="Stripe Location ID",
        help="Optional Stripe Terminal location identifier used for operational verification.",
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        domain="[('company_id', '=', company_id), ('type', 'in', ('bank', 'cash'))]",
        help="The journal used when Stripe confirms the card-present payment.",
    )
    payment_method_line_id = fields.Many2one(
        "account.payment.method.line",
        string="Incoming Payment Method",
        required=True,
        check_company=True,
        domain="[('id', 'in', available_payment_method_line_ids)]",
    )
    available_payment_method_line_ids = fields.Many2many(
        "account.payment.method.line",
        compute="_compute_available_payment_method_line_ids",
    )
    webhook_ready = fields.Boolean(compute="_compute_webhook_ready")

    _reader_id_unique = models.Constraint(
        "UNIQUE(reader_id)",
        "A Stripe Terminal reader can only be configured once.",
    )

    @api.depends("journal_id")
    def _compute_available_payment_method_line_ids(self):
        for config in self:
            config.available_payment_method_line_ids = config.journal_id.inbound_payment_method_line_ids

    @api.depends("provider_id.stripe_terminal_webhook_secret")
    def _compute_webhook_ready(self):
        for config in self:
            config.webhook_ready = bool(config.provider_id.sudo().stripe_terminal_webhook_secret)

    @api.constrains("provider_id", "company_id")
    def _check_provider_company(self):
        for config in self:
            if config.provider_id.code != "stripe" or config.provider_id.company_id != config.company_id:
                raise ValidationError(_("Select the Stripe provider for the same company as the reader."))

    @api.constrains("active", "reader_id", "provider_id")
    def _check_active_reader(self):
        for config in self:
            if config.active and not config.reader_id:
                raise ValidationError(_("Set or create a Stripe reader before activating this configuration."))
            if config.active and config.provider_state not in ("test", "enabled"):
                raise ValidationError(
                    _("Enable the Stripe provider or place it in Test Mode before activating a reader.")
                )

    @api.constrains("journal_id", "payment_method_line_id", "company_id")
    def _check_payment_route(self):
        for config in self:
            if config.journal_id.company_id != config.company_id:
                raise ValidationError(_("The payment journal must belong to the reader's company."))
            if config.payment_method_line_id not in config.journal_id.inbound_payment_method_line_ids:
                raise ValidationError(_("Select an incoming payment method from the configured journal."))

    @api.constrains("is_default", "company_id", "active")
    def _check_single_default(self):
        for config in self.filtered(lambda record: record.active and record.is_default):
            duplicate = self.search_count(
                [
                    ("id", "!=", config.id),
                    ("company_id", "=", config.company_id.id),
                    ("active", "=", True),
                    ("is_default", "=", True),
                ]
            )
            if duplicate:
                raise ValidationError(_("Only one active default Stripe reader is allowed per company."))

    def action_test_reader(self):
        self.ensure_one()
        if not self.reader_id:
            raise ValidationError(_("Set a Stripe reader identifier first."))
        reader = self.provider_id.sudo()._send_api_request("GET", f"terminal/readers/{self.reader_id}")
        if reader.get("id") != self.reader_id:
            raise ValidationError(_("Stripe returned a different reader identifier."))
        if self.location_id and reader.get("location") != self.location_id:
            raise ValidationError(_("The reader is not registered to the configured Stripe location."))
        expected_livemode = self.provider_state == "enabled"
        if bool(reader.get("livemode")) != expected_livemode:
            raise ValidationError(_("The reader and Stripe provider are not in the same live/test mode."))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Stripe Terminal"),
                "message": _(
                    "Reader %(reader)s is %(status)s.", reader=self.name, status=reader.get("status", "unknown")
                ),
                "type": "success" if reader.get("status") == "online" else "warning",
                "sticky": False,
            },
        }

    def action_create_simulated_reader(self):
        self.ensure_one()
        if self.provider_state != "test":
            raise ValidationError(_("Simulated readers can only be created with a Stripe provider in Test Mode."))
        if self.reader_id:
            raise ValidationError(_("This configuration already has a reader."))
        if not self.location_id:
            raise ValidationError(_("Set a Stripe test location before creating the simulated reader."))
        reader = self.provider_id.sudo()._send_api_request(
            "POST",
            "terminal/readers",
            data={
                "registration_code": "simulated-s710",
                "location": self.location_id,
                "label": self.name,
            },
        )
        if not reader.get("id"):
            raise ValidationError(_("Stripe did not return a simulated reader identifier."))
        self.reader_id = reader["id"]
        return self.action_test_reader()

    def action_create_terminal_webhook(self):
        self.ensure_one()
        if self.provider_id.sudo().stripe_terminal_webhook_secret:
            raise ValidationError(_("The dedicated Stripe Terminal webhook is already configured."))
        base_url = self.company_id.get_base_url().rstrip("/") + "/"
        webhook = self.provider_id.sudo()._send_api_request(
            "POST",
            "webhook_endpoints",
            data={
                "url": urljoin(base_url, "southern/stripe-terminal/webhook"),
                "enabled_events[]": [
                    "payment_intent.succeeded",
                    "payment_intent.canceled",
                    "terminal.reader.action_succeeded",
                    "terminal.reader.action_failed",
                ],
            },
        )
        if not webhook.get("secret"):
            raise ValidationError(_("Stripe did not return a webhook signing secret."))
        self.provider_id.sudo().stripe_terminal_webhook_secret = webhook["secret"]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Stripe Terminal"),
                "message": _("The signed Terminal webhook endpoint is configured."),
                "type": "success",
                "sticky": False,
            },
        }
