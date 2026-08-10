from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _default_southern_payment_type(self):
        if self.env.context.get("default_move_type") != "out_invoice":
            return False
        has_default_terminal = self.env["southern.stripe.terminal.config"].search_count(
            [
                ("company_id", "=", self.env.company.id),
                ("active", "=", True),
                ("is_default", "=", True),
            ],
            limit=1,
        )
        return "stripe_terminal" if has_default_terminal else False

    southern_payment_type = fields.Selection(
        [
            ("stripe_terminal", "Stripe Terminal"),
            ("stripe_moto", "Stripe Terminal - Pay by Phone"),
            ("cash", "Cash"),
            ("ach", "ACH"),
            ("online_link", "Online Payment Link"),
        ],
        string="Payment Type",
        default=_default_southern_payment_type,
        copy=False,
        tracking=True,
        help=(
            "Select Stripe Terminal or Stripe Terminal - Pay by Phone to add the configured "
            "processing fee to this draft invoice."
        ),
    )
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

    def _southern_sync_terminal_fee_line(self, *, strict=False):
        if self.env.context.get("southern_skip_processing_fee_sync"):
            return
        for move in self:
            if move.move_type != "out_invoice" or move.state != "draft":
                continue
            if move.southern_terminal_fee_payment_id:
                continue
            fee_lines = move.invoice_line_ids.filtered("southern_is_processing_fee")
            if move.southern_payment_type not in ("stripe_terminal", "stripe_moto"):
                if fee_lines:
                    fee_lines.with_context(southern_skip_processing_fee_sync=True).unlink()
                continue
            route = move._southern_processing_fee_route()
            if not route:
                if strict:
                    raise UserError(_("Enable the Stripe Terminal processing fee before posting."))
                continue
            if not route.processing_fee_income_account_id:
                if strict:
                    raise UserError(
                        _("Configure the Stripe Terminal fee income account before posting.")
                    )
                continue

            base_total = move.amount_total - sum(fee_lines.mapped("price_total"))
            fee_amount = 0.0
            if move.currency_id.compare_amounts(base_total, 0.0) > 0:
                fee_amount = move.currency_id.round(
                    (base_total * route.processing_fee_percentage / 100.0)
                    + route.processing_fee_fixed
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
                    (fee_lines - primary_line).with_context(
                        southern_skip_processing_fee_sync=True
                    ).unlink()
            else:
                move.with_context(southern_skip_processing_fee_sync=True).write(
                    {"invoice_line_ids": [Command.create(line_values)]}
                )

    @api.model_create_multi
    def create(self, vals_list):
        vals_list = [dict(vals) for vals in vals_list]
        terminal_company_ids = set()
        candidate_company_ids = {
            vals.get("company_id", self.env.company.id)
            for vals in vals_list
            if vals.get("move_type", self.env.context.get("default_move_type")) == "out_invoice"
            and "southern_payment_type" not in vals
        }
        if candidate_company_ids:
            terminal_company_ids = set(
                self.env["southern.stripe.terminal.config"]
                .search(
                    [
                        ("company_id", "in", list(candidate_company_ids)),
                        ("active", "=", True),
                        ("is_default", "=", True),
                    ]
                )
                .mapped("company_id")
                .ids
            )
        for vals in vals_list:
            move_type = vals.get("move_type", self.env.context.get("default_move_type"))
            company_id = vals.get("company_id", self.env.company.id)
            if (
                move_type == "out_invoice"
                and "southern_payment_type" not in vals
                and company_id in terminal_company_ids
            ):
                vals["southern_payment_type"] = "stripe_terminal"
        moves = super().create(vals_list)
        moves._southern_sync_terminal_fee_line()
        return moves

    def write(self, vals):
        result = super().write(vals)
        if (
            {"invoice_line_ids", "southern_payment_type"} & set(vals)
            and not self.env.context.get("southern_skip_processing_fee_sync")
        ):
            self._southern_sync_terminal_fee_line()
        return result

    def action_post(self):
        self._southern_sync_terminal_fee_line(strict=True)
        return super().action_post()

    def _southern_validate_payment_invoice(self):
        self.ensure_one()
        if self.move_type != "out_invoice" or self.state != "posted":
            raise UserError(_("Payments can only be registered on posted customer invoices."))
        if self.payment_state in ("paid", "reversed") or self.amount_residual <= 0:
            raise UserError(_("This invoice has no remaining balance to pay."))

    def _southern_open_payment_route(self, route_name):
        self._southern_validate_payment_invoice()
        self._southern_validate_selected_payment_type(route_name)
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

    def _southern_validate_selected_payment_type(self, payment_type):
        self.ensure_one()
        terminal_types = {"stripe_terminal", "stripe_moto"}
        if self.southern_payment_type in terminal_types and payment_type in terminal_types:
            if self.southern_payment_type != payment_type:
                self.southern_payment_type = payment_type
            return
        if self.southern_payment_type and self.southern_payment_type != payment_type:
            selected = dict(self._fields["southern_payment_type"].selection).get(
                self.southern_payment_type,
                self.southern_payment_type,
            )
            raise UserError(
                _(
                    "This invoice is marked for %(selected)s. Reset it to draft to change the payment type.",
                    selected=selected,
                )
            )
        if not self.southern_payment_type:
            self.southern_payment_type = payment_type

    def action_pay_with_cash(self):
        return self._southern_open_payment_route("cash")

    def action_pay_with_ach(self):
        return self._southern_open_payment_route("ach")

    def _southern_pay_with_stripe_terminal(self, *, payment_type, payment_mode):
        self._southern_validate_payment_invoice()
        self._southern_validate_selected_payment_type(payment_type)

        if payment_mode == "moto" and not self.env.user.has_group(
            "southern_stripe_terminal.group_stripe_terminal_moto"
        ):
            raise AccessError(_("You are not authorized to accept Stripe payments by phone."))

        existing = self.env["southern.stripe.terminal.payment"].search(
            [
                ("invoice_id", "=", self.id),
                ("payment_mode", "=", payment_mode),
                ("state", "in", ("draft", "in_progress", "stripe_succeeded", "failed", "needs_review")),
            ],
            order="id desc",
            limit=1,
        )
        if existing:
            return existing.action_open_form()

        config_domain = [
            ("company_id", "=", self.company_id.id),
            ("active", "=", True),
            ("is_default", "=", True),
        ]
        if payment_mode == "moto":
            config_domain.append(("moto_enabled", "=", True))
        config = self.env["southern.stripe.terminal.config"].search(config_domain, limit=1)
        if not config:
            if payment_mode == "moto":
                raise UserError(
                    _(
                        "Enable MOTO on the company's default Stripe reader only after Stripe "
                        "Support approves telephone payments."
                    )
                )
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
                "payment_mode": payment_mode,
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

    def action_pay_with_stripe_terminal(self):
        return self._southern_pay_with_stripe_terminal(
            payment_type="stripe_terminal",
            payment_mode="card_present",
        )

    def action_pay_by_phone(self):
        return self._southern_pay_with_stripe_terminal(
            payment_type="stripe_moto",
            payment_mode="moto",
        )


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    southern_is_processing_fee = fields.Boolean(
        string="Transaction Processing Fee Line",
        default=False,
        copy=False,
        index=True,
    )
