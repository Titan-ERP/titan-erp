import uuid

from odoo import Command, _, api, fields, models
from odoo.addons.payment import utils as payment_utils
from odoo.exceptions import AccessError, UserError, ValidationError

from ..utils import stripe_terminal_payment_method_type, stripe_terminal_process_data


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
    payment_mode = fields.Selection(
        [
            ("card_present", "Card Present"),
            ("moto", "Phone / Mail Order (MOTO)"),
        ],
        string="Terminal Mode",
        required=True,
        default="card_present",
        readonly=True,
        copy=False,
        index=True,
        tracking=True,
    )
    invoice_amount = fields.Monetary(
        string="Invoice Balance",
        readonly=True,
        copy=False,
        tracking=True,
    )
    processing_fee_amount = fields.Monetary(
        string="Stripe Terminal Processing Fee",
        readonly=True,
        copy=False,
        tracking=True,
    )
    processing_fee_embedded = fields.Boolean(
        string="Fee Already on Invoice",
        readonly=True,
        copy=False,
    )
    processing_fee_percentage = fields.Float(readonly=True, copy=False, digits=(16, 4))
    processing_fee_fixed = fields.Monetary(readonly=True, copy=False)
    processing_fee_name = fields.Char(readonly=True, copy=False)
    processing_fee_income_account_id = fields.Many2one(
        "account.account",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
    )
    processing_fee_tax_ids = fields.Many2many(
        "account.tax",
        relation="southern_terminal_payment_fee_tax_rel",
        column1="payment_id",
        column2="tax_id",
        readonly=True,
        copy=False,
        check_company=True,
    )
    fee_invoice_id = fields.Many2one(
        "account.move",
        string="Processing Fee Invoice",
        readonly=True,
        copy=False,
        check_company=True,
        ondelete="restrict",
        tracking=True,
    )
    amount = fields.Monetary(required=True, tracking=True)
    # res.currency is global in Odoo 19 and has no company_id. Currency/company
    # consistency is enforced explicitly by _check_payment_identity below.
    currency_id = fields.Many2one("res.currency", required=True)
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

    @api.model_create_multi
    def create(self, vals_list):
        if any(values.get("payment_mode", "card_present") == "moto" for values in vals_list):
            self._require_moto_access()
        return super().create(vals_list)

    def write(self, vals):
        if "payment_mode" in vals and any(
            payment.payment_mode != vals["payment_mode"] for payment in self
        ):
            raise ValidationError(_("A Stripe Terminal payment mode cannot be changed after creation."))
        return super().write(vals)

    def _require_moto_access(self):
        if not self.env.user.has_group("southern_stripe_terminal.group_stripe_terminal_moto"):
            raise AccessError(_("You are not authorized to accept Stripe payments by phone."))

    def _validate_moto_configuration(self):
        self.ensure_one()
        if self.payment_mode != "moto":
            return
        self._require_moto_access()
        if not self.config_id.moto_enabled:
            raise UserError(
                _(
                    "MOTO is not enabled on this reader configuration. Stripe Support must "
                    "approve telephone payments before this option is used."
                )
            )

    @api.constrains(
        "invoice_id",
        "config_id",
        "payment_mode",
        "currency_id",
        "invoice_amount",
        "processing_fee_amount",
        "processing_fee_embedded",
        "processing_fee_income_account_id",
        "amount",
    )
    def _check_payment_identity(self):
        for payment in self:
            if payment.invoice_id.move_type != "out_invoice" or payment.invoice_id.state != "posted":
                raise ValidationError(_("Terminal payments require a posted customer invoice."))
            if payment.config_id.company_id != payment.invoice_id.company_id:
                raise ValidationError(_("The reader and invoice must belong to the same company."))
            if payment.payment_mode == "moto" and not payment.config_id.moto_enabled:
                raise ValidationError(_("MOTO is not enabled on this Stripe Terminal reader."))
            if payment.currency_id != payment.invoice_id.currency_id:
                raise ValidationError(_("The terminal payment currency must match the invoice currency."))
            if payment.currency_id != payment.config_id.currency_id:
                raise ValidationError(_("Stripe Terminal payments must use the company's local currency."))
            if payment.amount <= 0:
                raise ValidationError(_("The terminal payment amount must be positive."))
            if payment.invoice_amount <= 0:
                raise ValidationError(_("The invoice balance captured for the terminal must be positive."))
            if payment.processing_fee_amount < 0:
                raise ValidationError(_("The Stripe Terminal processing fee cannot be negative."))
            if (
                payment.processing_fee_amount
                and not payment.processing_fee_embedded
                and not payment.processing_fee_income_account_id
            ):
                raise ValidationError(_("The Stripe Terminal processing fee requires an income account."))
            expected_amount = payment.invoice_amount
            if not payment.processing_fee_embedded:
                expected_amount += payment.processing_fee_amount
            if payment.currency_id.compare_amounts(payment.amount, expected_amount):
                raise ValidationError(_("The terminal total must equal the invoice balance plus its terminal fee."))

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
                "payment_method_types[]": stripe_terminal_payment_method_type(self.payment_mode),
                "capture_method": "automatic",
                "metadata[odoo_terminal_payment_id]": str(self.id),
                "metadata[odoo_company_id]": str(self.company_id.id),
                "metadata[odoo_invoice_amount]": str(self.invoice_amount),
                "metadata[odoo_terminal_fee]": str(self.processing_fee_amount),
                "metadata[odoo_terminal_mode]": self.payment_mode,
            },
            idempotency_key=f"odoo-terminal-intent-{self.idempotency_key}",
        )

    def _stripe_process_intent(self):
        self.ensure_one()
        return self.provider_id.sudo()._send_api_request(
            "POST",
            f"terminal/readers/{self.reader_id}/process_payment_intent",
            data=stripe_terminal_process_data(self.payment_intent_id, self.payment_mode),
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
        self._validate_moto_configuration()
        if self.state != "draft":
            raise UserError(_("Only a draft terminal payment can be sent to a reader."))
        if self.invoice_id.payment_state in ("paid", "reversed"):
            raise UserError(_("The invoice is already settled."))
        if self.currency_id.compare_amounts(self.invoice_amount, self.invoice_id.amount_residual):
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
        payment_label = _("phone payment") if self.payment_mode == "moto" else _("in-person payment")
        self.invoice_id.message_post(
            body=_(
                "Stripe Terminal %(payment_label)s sent to reader %(reader)s.",
                payment_label=payment_label,
                reader=self.config_id.name,
            )
        )
        return self.action_open_form()

    def action_retry(self):
        self.ensure_one()
        self._validate_moto_configuration()
        if self.state != "failed":
            raise UserError(_("Only a failed terminal payment can be retried."))
        if self.currency_id.compare_amounts(self.invoice_amount, self.invoice_id.amount_residual):
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

    def _ensure_processing_fee_invoice(self):
        self.ensure_one()
        if self.processing_fee_embedded or self.currency_id.is_zero(self.processing_fee_amount):
            return self.env["account.move"]
        fee_invoice = self.fee_invoice_id
        if not fee_invoice:
            fee_invoice = self.env["account.move"].search(
                [("southern_terminal_fee_payment_id", "=", self.id)],
                limit=1,
            )
        if not fee_invoice:
            fee_invoice = self.env["account.move"].create(
                {
                    "move_type": "out_invoice",
                    "company_id": self.company_id.id,
                    "journal_id": self.invoice_id.journal_id.id,
                    "partner_id": self.partner_id.id,
                    "currency_id": self.currency_id.id,
                    "invoice_date": fields.Date.context_today(self),
                    "invoice_origin": self.invoice_id.name,
                    "ref": _("Stripe Terminal fee for %(invoice)s", invoice=self.invoice_id.name),
                    "southern_terminal_fee_payment_id": self.id,
                    "invoice_line_ids": [
                        Command.create(
                            {
                                "name": self.processing_fee_name
                                or _("Stripe Terminal Processing Fee"),
                                "quantity": 1.0,
                                "price_unit": self.processing_fee_amount,
                                "account_id": self.processing_fee_income_account_id.id,
                                "tax_ids": [Command.set(self.processing_fee_tax_ids.ids)],
                                "southern_is_processing_fee": True,
                                "southern_manual_revenue_bucket": "fees",
                            }
                        )
                    ],
                }
            )
        if fee_invoice.state == "draft":
            fee_invoice.action_post()
        if fee_invoice.state != "posted":
            raise UserError(_("The Stripe Terminal fee invoice could not be posted."))
        if self.fee_invoice_id != fee_invoice:
            self.fee_invoice_id = fee_invoice
        return fee_invoice

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
        if self.currency_id.compare_amounts(self.invoice_amount, invoice.amount_residual):
            self.write(
                {
                    "state": "needs_review",
                    "stripe_failure_message": _(
                        "Stripe succeeded, but the invoice balance changed before registration."
                    ),
                }
            )
            return self.env["account.payment"]

        fee_invoice = self._ensure_processing_fee_invoice()
        invoices = invoice | fee_invoice
        total_residual = sum(invoices.mapped("amount_residual"))
        if self.currency_id.compare_amounts(self.amount, total_residual):
            self.write(
                {
                    "state": "needs_review",
                    "stripe_failure_message": _(
                        "Stripe succeeded, but the combined invoice and terminal-fee balance changed."
                    ),
                }
            )
            return self.env["account.payment"]

        payment_channel = _("Stripe Terminal MOTO") if self.payment_mode == "moto" else _("Stripe Terminal")
        wizard = (
            self.env["account.payment.register"]
            .with_company(self.company_id)
            .with_context(active_model="account.move", active_ids=invoices.ids)
            .create(
                {
                    "journal_id": self.config_id.journal_id.id,
                    "payment_method_line_id": self.config_id.payment_method_line_id.id,
                    "amount": self.amount,
                    "currency_id": self.currency_id.id,
                    "payment_date": fields.Date.context_today(self),
                    "communication": _(
                        "%(payment_channel)s %(intent)s",
                        payment_channel=payment_channel,
                        intent=self.payment_intent_id,
                    ),
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
                "%(payment_channel)s payment completed and registered as %(payment)s, including a processing fee of %(fee)s.",
                payment_channel=payment_channel,
                payment=account_payment.display_name,
                fee=self.processing_fee_amount,
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
                try:
                    payment._register_odoo_payment()
                except (UserError, ValidationError) as error:
                    payment.write(
                        {
                            "state": "needs_review",
                            "stripe_failure_message": str(error),
                        }
                    )
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
