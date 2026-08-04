from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    southern_processing_fee_amount = fields.Monetary(
        string="Transaction Processing Fee",
        compute="_compute_southern_processing_fee_amount",
        currency_field="currency_id",
    )
    southern_terminal_fee_payment_id = fields.Many2one(
        "southern.stripe.terminal.payment",
        string="Stripe Terminal Fee Payment",
        readonly=True,
        copy=False,
        index=True,
        ondelete="restrict",
        help="Terminal payment that created this supplemental processing-fee invoice.",
    )

    _southern_terminal_fee_payment_unique = models.Constraint(
        "UNIQUE(southern_terminal_fee_payment_id)",
        "A Stripe Terminal payment can create only one processing-fee invoice.",
    )

    @api.depends(
        "invoice_line_ids.price_total",
        "invoice_line_ids.southern_is_processing_fee",
    )
    def _compute_southern_processing_fee_amount(self):
        for move in self:
            move.southern_processing_fee_amount = sum(
                move.invoice_line_ids.filtered("southern_is_processing_fee").mapped("price_total")
            )

    def _southern_processing_fee_route(self):
        self.ensure_one()
        return self.env["southern.invoice.payment.route"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("processing_fee_enabled", "=", True),
            ],
            limit=1,
        )

    def _southern_terminal_fee_snapshot(self):
        """Return the fee captured only when a Stripe Terminal payment starts."""
        self.ensure_one()
        fee_lines = self.invoice_line_ids.filtered("southern_is_processing_fee")
        embedded_fee = sum(fee_lines.mapped("price_total"))
        if not self.currency_id.is_zero(embedded_fee):
            primary_line = fee_lines[:1]
            return {
                "processing_fee_amount": embedded_fee,
                "processing_fee_embedded": True,
                "processing_fee_name": primary_line.name or _("Stripe Terminal Processing Fee"),
                "processing_fee_income_account_id": primary_line.account_id.id,
                "processing_fee_tax_ids": primary_line.tax_ids.ids,
                "processing_fee_percentage": 0.0,
                "processing_fee_fixed": 0.0,
            }

        route = self._southern_processing_fee_route()
        if not route:
            return {
                "processing_fee_amount": 0.0,
                "processing_fee_embedded": False,
            }
        if not route.processing_fee_income_account_id:
            raise UserError(_("Configure the Stripe Terminal fee income account before collecting payment."))
        fee_amount = self.currency_id.round(
            (self.amount_residual * route.processing_fee_percentage / 100.0)
            + route.processing_fee_fixed
        )
        return {
            "processing_fee_amount": fee_amount,
            "processing_fee_embedded": False,
            "processing_fee_name": route.processing_fee_name,
            "processing_fee_income_account_id": route.processing_fee_income_account_id.id,
            "processing_fee_tax_ids": route.processing_fee_tax_ids.ids,
            "processing_fee_percentage": route.processing_fee_percentage,
            "processing_fee_fixed": route.processing_fee_fixed,
        }

    def _southern_validate_payment_invoice(self):
        self.ensure_one()
        if self.move_type != "out_invoice" or self.state != "posted":
            raise UserError(_("Payments can only be registered on posted customer invoices."))
        if self.payment_state in ("paid", "reversed") or self.amount_residual <= 0:
            raise UserError(_("This invoice has no remaining balance to pay."))

    def _southern_open_payment_route(self, route_name):
        self._southern_validate_payment_invoice()
        route = self.env["southern.invoice.payment.route"].search(
            [("company_id", "=", self.company_id.id)],
            limit=1,
        )
        if not route:
            raise UserError(_("Configure Cash and ACH payment routes for this company first."))
        if route_name == "cash":
            journal = route.cash_journal_id
            payment_method_line = route.cash_payment_method_line_id
        elif route_name == "ach":
            journal = route.ach_journal_id
            payment_method_line = route.ach_payment_method_line_id
        else:
            raise UserError(_("Unsupported invoice payment route."))

        action = self.action_register_payment()
        action["context"] = {
            **(action.get("context") or {}),
            "default_journal_id": journal.id,
            "default_payment_method_line_id": payment_method_line.id,
        }
        return action

    def action_pay_with_cash(self):
        return self._southern_open_payment_route("cash")

    def action_pay_with_ach(self):
        return self._southern_open_payment_route("ach")

    def action_pay_with_stripe_terminal(self):
        self._southern_validate_payment_invoice()

        existing = self.env["southern.stripe.terminal.payment"].search(
            [
                ("invoice_id", "=", self.id),
                ("state", "in", ("draft", "in_progress", "stripe_succeeded", "failed", "needs_review")),
            ],
            order="id desc",
            limit=1,
        )
        if existing:
            return existing.action_open_form()

        config = self.env["southern.stripe.terminal.config"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("active", "=", True),
                ("is_default", "=", True),
            ],
            limit=1,
        )
        if not config:
            raise UserError(_("Configure one active default Stripe Terminal reader for this company."))
        busy_reader = self.env["southern.stripe.terminal.payment"].search(
            [("config_id", "=", config.id), ("state", "=", "in_progress")],
            limit=1,
        )
        if busy_reader:
            raise UserError(
                _(
                    "Reader %(reader)s is already processing %(invoice)s.",
                    reader=config.name,
                    invoice=busy_reader.invoice_id.name,
                )
            )

        fee_snapshot = self._southern_terminal_fee_snapshot()
        invoice_amount = self.amount_residual
        fee_amount = fee_snapshot.get("processing_fee_amount", 0.0)
        terminal_amount = invoice_amount
        if not fee_snapshot.get("processing_fee_embedded"):
            terminal_amount += fee_amount
        terminal_payment = self.env["southern.stripe.terminal.payment"].create(
            {
                "invoice_id": self.id,
                "config_id": config.id,
                "invoice_amount": invoice_amount,
                "processing_fee_amount": fee_amount,
                "processing_fee_embedded": fee_snapshot.get("processing_fee_embedded", False),
                "processing_fee_name": fee_snapshot.get("processing_fee_name"),
                "processing_fee_income_account_id": fee_snapshot.get(
                    "processing_fee_income_account_id"
                ),
                "processing_fee_tax_ids": [
                    (6, 0, fee_snapshot.get("processing_fee_tax_ids", []))
                ],
                "processing_fee_percentage": fee_snapshot.get("processing_fee_percentage", 0.0),
                "processing_fee_fixed": fee_snapshot.get("processing_fee_fixed", 0.0),
                "amount": self.currency_id.round(terminal_amount),
                "currency_id": self.currency_id.id,
            }
        )
        terminal_payment.action_send_to_reader()
        return terminal_payment.action_open_form()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    southern_is_processing_fee = fields.Boolean(
        string="Transaction Processing Fee Line",
        default=False,
        copy=False,
        index=True,
    )
