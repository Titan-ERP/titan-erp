from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    southern_processing_fee_amount = fields.Monetary(
        string="Transaction Processing Fee",
        compute="_compute_southern_processing_fee_amount",
        currency_field="currency_id",
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

    def _southern_sync_processing_fee(self, *, strict=False):
        if self.env.context.get("southern_skip_processing_fee_sync"):
            return
        for move in self:
            if move.move_type != "out_invoice" or move.state != "draft":
                continue
            route = move._southern_processing_fee_route()
            fee_lines = move.invoice_line_ids.filtered("southern_is_processing_fee")
            if not route:
                if fee_lines:
                    fee_lines.with_context(southern_skip_processing_fee_sync=True).unlink()
                continue
            if not route.processing_fee_income_account_id:
                if strict:
                    raise UserError(_("Configure the transaction processing fee income account before posting."))
                continue

            base_total = move.amount_total - sum(fee_lines.mapped("price_total"))
            fee_amount = 0.0
            if move.currency_id.compare_amounts(base_total, 0.0) > 0:
                fee_amount = move.currency_id.round(
                    (base_total * route.processing_fee_percentage / 100.0) + route.processing_fee_fixed
                )

            if move.currency_id.is_zero(fee_amount):
                if fee_lines:
                    fee_lines.with_context(southern_skip_processing_fee_sync=True).unlink()
                continue

            line_values = {
                "name": route.processing_fee_name,
                "quantity": 1.0,
                "price_unit": fee_amount,
                "account_id": route.processing_fee_income_account_id.id,
                "tax_ids": [Command.set(route.processing_fee_tax_ids.ids)],
                "southern_is_processing_fee": True,
                "southern_manual_revenue_bucket": "fees",
                "sequence": 999,
            }
            primary_line = fee_lines[:1]
            if primary_line:
                primary_line.with_context(southern_skip_processing_fee_sync=True).write(line_values)
                if len(fee_lines) > 1:
                    (fee_lines - primary_line).with_context(southern_skip_processing_fee_sync=True).unlink()
            else:
                move.with_context(southern_skip_processing_fee_sync=True).write(
                    {"invoice_line_ids": [Command.create(line_values)]}
                )

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        moves._southern_sync_processing_fee()
        return moves

    def write(self, vals):
        result = super().write(vals)
        if "invoice_line_ids" in vals and not self.env.context.get("southern_skip_processing_fee_sync"):
            self._southern_sync_processing_fee()
        return result

    def action_update_processing_fee(self):
        self._southern_sync_processing_fee(strict=True)
        return True

    def action_post(self):
        self._southern_sync_processing_fee(strict=True)
        return super().action_post()

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


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    southern_is_processing_fee = fields.Boolean(
        string="Transaction Processing Fee Line",
        default=False,
        copy=False,
        index=True,
    )
