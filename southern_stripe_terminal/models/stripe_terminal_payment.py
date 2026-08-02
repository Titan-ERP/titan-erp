import uuid

from odoo import _, api, fields, models
from odoo.addons.payment import utils as payment_utils
from odoo.exceptions import UserError, ValidationError


class SouthernStripeTerminalPayment(models.Model):
    _name = "southern.stripe.terminal.payment"
    _description = "Stripe Terminal Invoice Payment"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model declaration
    _order = "id desc"
    _check_company_auto = True

    invoice_id = fields.Many2one(
        "account.move",
        required=True,
        check_company=True,
        index=True,
        ondelete="restrict",
        tracking=True,
    )
    company_id = fields.Many2one(related="invoice_id.company_id", store=True, index=True)
    partner_id = fields.Many2one(related="invoice_id.partner_id", store=True)
    config_id = fields.Many2one(
        "southern.stripe.terminal.config",
        required=True,
        check_company=True,
        ondelete="restrict",
    )
    provider_id = fields.Many2one(related="config_id.provider_id", store=True)
    provider_state = fields.Selection(
        related="provider_id.state",
        string="Stripe Provider State",
        readonly=True,
    )
    reader_id = fields.Char(related="config_id.reader_id", store=True)
    amount = fields.Monetary(required=True, tracking=True)
    currency_id = fields.Many2one("res.currency", required=True, check_company=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("in_progress", "Waiting for Card"),
            ("stripe_succeeded", "Stripe Succeeded"),
            ("registered", "Registered in Odoo"),
            ("failed", "Failed"),
            ("canceled", "Canceled"),
            ("needs_review", "Needs Review"),
        ],
        default="draft",
        required=True,
        index=True,
        copy=False,
        tracking=True,
    )
    idempotency_key = fields.Char(
        required=True,
        default=lambda self: uuid.uuid4().hex,
        readonly=True,
        copy=False,
        index=True,
    )
    payment_intent_id = fields.Char(readonly=True, copy=False, index=True, tracking=True)
    stripe_status = fields.Char(readonly=True, copy=False)
    reader_action_status = fields.Char(readonly=True, copy=False)
    stripe_failure_code = fields.Char(readonly=True, copy=False)
    stripe_failure_message = fields.Char(readonly=True, copy=False)
    account_payment_id = fields.Many2one(
        "account.payment",
        string="Odoo Payment",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
        tracking=True,
    )
    started_at = fields.Datetime(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    retry_count = fields.Integer(default=0, readonly=True, copy=False)

    _idempotency_key_unique = models.Constraint(
        "UNIQUE(idempotency_key)",
        "The terminal idempotency key must be unique.",
    )
    _payment_intent_id_unique = models.Constraint(
        "UNIQUE(payment_intent_id)",
        "The Stripe PaymentIntent is already linked.",
    )

    @api.constrains("invoice_id", "config_id", "currency_id", "amount")
    def _check_payment_identity(self):
        for payment in self:
            if payment.invoice_id.move_type != "out_invoice" or payment.invoice_id.state != "posted":
                raise ValidationError(_("Terminal payments require a posted customer invoice."))
            if payment.config_id.company_id != payment.invoice_id.company_id:
                raise ValidationError(_("The reader and invoice must belong to the same company."))
            if payment.currency_id != payment.invoice_id.currency_id:
                raise ValidationError(_("The terminal payment currency must match the invoice currency."))
            if payment.currency_id != payment.config_id.currency_id:
                raise ValidationError(_("Stripe Terminal payments must use the company's local currency."))
            if payment.amount <= 0:
                raise ValidationError(_("The terminal payment amount must be positive."))

    def action_open_form(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Stripe Terminal Payment"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "current",
        }

    def _stripe_create_intent(self):
        self.ensure_one()
        minor_amount = payment_utils.to_minor_currency_units(self.amount, self.currency_id)
        return self.provider_id.sudo()._send_api_request(
            "POST",
            "payment_intents",
            data={
                "amount": minor_amount,
                "currency": self.currency_id.name.lower(),
                "payment_method_types[]": "card_present",
                "capture_method": "automatic",
                "metadata[odoo_terminal_payment_id]": str(self.id),
                "metadata[odoo_company_id]": str(self.company_id.id),
            },
            idempotency_key=f"odoo-terminal-intent-{self.idempotency_key}",
        )

    def _stripe_process_intent(self):
        self.ensure_one()
        return self.provider_id.sudo()._send_api_request(
            "POST",
            f"terminal/readers/{self.reader_id}/process_payment_intent",
            data={"payment_intent": self.payment_intent_id},
            idempotency_key=f"odoo-terminal-process-{self.idempotency_key}-{self.retry_count}",
        )

    def _mark_failed(self, message):
        self.ensure_one()
        self.write(
            {
                "state": "failed",
                "stripe_failure_message": str(message),
                "completed_at": fields.Datetime.now(),
            }
        )

    def action_send_to_reader(self):
        self.ensure_one()
        if self.state != "draft":
            raise UserError(_("Only a draft terminal payment can be sent to a reader."))
        if self.invoice_id.payment_state in ("paid", "reversed"):
            raise UserError(_("The invoice is already settled."))
        if self.currency_id.compare_amounts(self.amount, self.invoice_id.amount_residual):
            raise UserError(_("The invoice balance changed. Start a new terminal payment."))

        try:
            intent = self._stripe_create_intent()
        except ValidationError as error:
            self._mark_failed(error)
            return self.action_open_form()
        if not intent.get("id"):
            raise UserError(_("Stripe did not return a PaymentIntent identifier."))
        self.write(
            {
                "payment_intent_id": intent["id"],
                "stripe_status": intent.get("status"),
                "started_at": fields.Datetime.now(),
            }
        )
        try:
            reader = self._stripe_process_intent()
        except ValidationError as error:
            self._mark_failed(error)
            return self.action_open_form()
        action = reader.get("action") or {}
        self.write(
            {
                "state": "in_progress",
                "reader_action_status": action.get("status", "in_progress"),
                "stripe_failure_code": False,
                "stripe_failure_message": False,
            }
        )
        self.invoice_id.message_post(
            body=_("Stripe Terminal payment sent to reader %(reader)s.", reader=self.config_id.name)
        )
        return self.action_open_form()

    def action_retry(self):
        self.ensure_one()
        if self.state != "failed":
            raise UserError(_("Only a failed terminal payment can be retried."))
        if self.currency_id.compare_amounts(self.amount, self.invoice_id.amount_residual):
            raise UserError(_("The invoice balance changed. Cancel this attempt and start a new payment."))
        busy_reader = self.search(
            [("id", "!=", self.id), ("config_id", "=", self.config_id.id), ("state", "=", "in_progress")],
            limit=1,
        )
        if busy_reader:
            raise UserError(_("The reader is processing another invoice."))
        if not self.payment_intent_id:
            self.state = "draft"
            return self.action_send_to_reader()
        self.retry_count += 1
        try:
            reader = self._stripe_process_intent()
        except ValidationError as error:
            self._mark_failed(error)
            return self.action_open_form()
        action = reader.get("action") or {}
        self.write(
            {
                "state": "in_progress",
                "reader_action_status": action.get("status", "in_progress"),
                "stripe_failure_code": False,
                "stripe_failure_message": False,
                "completed_at": False,
            }
        )
        return self.action_open_form()

    def _retrieve_intent(self):
        self.ensure_one()
        if not self.payment_intent_id:
            raise UserError(_("No Stripe PaymentIntent is linked to this record."))
        return self.provider_id.sudo()._send_api_request("GET", f"payment_intents/{self.payment_intent_id}")

    def _register_odoo_payment(self):
        self.ensure_one()
        if self.account_payment_id:
            return self.account_payment_id
        invoice = self.invoice_id
        if invoice.payment_state in ("paid", "reversed") or invoice.amount_residual <= 0:
            self.write(
                {
                    "state": "needs_review",
                    "stripe_failure_message": _("Stripe succeeded after the invoice was already settled."),
                }
            )
            return self.env["account.payment"]
        if self.currency_id.compare_amounts(self.amount, invoice.amount_residual):
            self.write(
                {
                    "state": "needs_review",
                    "stripe_failure_message": _(
                        "Stripe succeeded, but the invoice balance changed before registration."
                    ),
                }
            )
            return self.env["account.payment"]

        wizard = (
            self.env["account.payment.register"]
            .with_company(self.company_id)
            .with_context(active_model="account.move", active_ids=invoice.ids)
            .create(
                {
                    "journal_id": self.config_id.journal_id.id,
                    "payment_method_line_id": self.config_id.payment_method_line_id.id,
                    "amount": self.amount,
                    "currency_id": self.currency_id.id,
                    "payment_date": fields.Date.context_today(self),
                    "communication": _("Stripe Terminal %(intent)s", intent=self.payment_intent_id),
                    "group_payment": True,
                }
            )
        )
        account_payments = wizard._create_payments()
        account_payment = account_payments[:1]
        if not account_payment:
            raise UserError(_("Odoo did not create the accounting payment."))
        self.write(
            {
                "account_payment_id": account_payment.id,
                "state": "registered",
                "completed_at": fields.Datetime.now(),
                "stripe_failure_code": False,
                "stripe_failure_message": False,
            }
        )
        invoice.message_post(
            body=_(
                "Stripe Terminal payment completed and registered as %(payment)s.",
                payment=account_payment.display_name,
            )
        )
        return account_payment

    def _stripe_refresh_and_finalize(self):
        for payment in self:
            if payment.state == "registered":
                continue
            intent = payment._retrieve_intent()
            status = intent.get("status")
            payment.write({"stripe_status": status})
            if status == "succeeded":
                payment.state = "stripe_succeeded"
                payment._register_odoo_payment()
            elif status == "canceled":
                payment.write({"state": "canceled", "completed_at": fields.Datetime.now()})
            elif status in ("requires_payment_method", "requires_confirmation") and payment.state != "in_progress":
                payment.state = "failed"
        return True

    def action_refresh_status(self):
        self._stripe_refresh_and_finalize()
        return self.action_open_form() if len(self) == 1 else True

    def action_cancel(self):
        self.ensure_one()
        if self.state not in ("draft", "in_progress", "failed"):
            raise UserError(_("This terminal payment can no longer be canceled."))
        if self.state == "in_progress":
            self.provider_id.sudo()._send_api_request(
                "POST",
                f"terminal/readers/{self.reader_id}/cancel_action",
                idempotency_key=f"odoo-terminal-cancel-reader-{self.idempotency_key}",
            )
        if self.payment_intent_id:
            intent = self._retrieve_intent()
            if intent.get("status") not in ("succeeded", "canceled"):
                self.provider_id.sudo()._send_api_request(
                    "POST",
                    f"payment_intents/{self.payment_intent_id}/cancel",
                    idempotency_key=f"odoo-terminal-cancel-intent-{self.idempotency_key}",
                )
        self.write({"state": "canceled", "completed_at": fields.Datetime.now()})
        return self.action_open_form()

    def action_simulate_card(self):
        self.ensure_one()
        if self.provider_state != "test":
            raise UserError(_("Card simulation is only available with a Stripe provider in Test Mode."))
        if self.state != "in_progress":
            raise UserError(_("The simulated reader is not waiting for a card."))
        self.provider_id.sudo()._send_api_request(
            "POST",
            f"test_helpers/terminal/readers/{self.reader_id}/present_payment_method",
        )
        self._stripe_refresh_and_finalize()
        return self.action_open_form()

    @api.model
    def _cron_refresh_pending(self):
        pending = self.search([("state", "in", ("in_progress", "stripe_succeeded"))], limit=50)
        pending._stripe_refresh_and_finalize()
