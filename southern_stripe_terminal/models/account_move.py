from odoo import _, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

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

        terminal_payment = self.env["southern.stripe.terminal.payment"].create(
            {
                "invoice_id": self.id,
                "config_id": config.id,
                "amount": self.amount_residual,
                "currency_id": self.currency_id.id,
            }
        )
        terminal_payment.action_send_to_reader()
        return terminal_payment.action_open_form()
