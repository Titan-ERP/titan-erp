from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SouthernInvoicePaymentRoute(models.Model):
    _name = "southern.invoice.payment.route"
    _description = "Invoice Payment Route"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    cash_journal_id = fields.Many2one(
        "account.journal",
        string="Cash Journal",
        required=True,
        check_company=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'cash')]",
    )
    cash_payment_method_line_id = fields.Many2one(
        "account.payment.method.line",
        string="Cash Incoming Payment Method",
        required=True,
        check_company=True,
        domain="[('id', 'in', available_cash_payment_method_line_ids)]",
    )
    available_cash_payment_method_line_ids = fields.Many2many(
        "account.payment.method.line",
        compute="_compute_available_payment_method_lines",
        relation="southern_invoice_payment_route_cash_method_rel",
    )
    ach_journal_id = fields.Many2one(
        "account.journal",
        string="ACH Bank Journal",
        required=True,
        check_company=True,
        domain="[('company_id', '=', company_id), ('type', '=', 'bank')]",
    )
    ach_payment_method_line_id = fields.Many2one(
        "account.payment.method.line",
        string="ACH Incoming Payment Method",
        required=True,
        check_company=True,
        domain="[('id', 'in', available_ach_payment_method_line_ids)]",
    )
    available_ach_payment_method_line_ids = fields.Many2many(
        "account.payment.method.line",
        compute="_compute_available_payment_method_lines",
        relation="southern_invoice_payment_route_ach_method_rel",
    )

    _company_unique = models.Constraint(
        "UNIQUE(company_id)",
        "Configure only one invoice payment route per company.",
    )

    @api.depends("cash_journal_id", "ach_journal_id")
    def _compute_available_payment_method_lines(self):
        for route in self:
            route.available_cash_payment_method_line_ids = (
                route.cash_journal_id.inbound_payment_method_line_ids
            )
            route.available_ach_payment_method_line_ids = (
                route.ach_journal_id.inbound_payment_method_line_ids
            )

    @api.constrains(
        "company_id",
        "cash_journal_id",
        "cash_payment_method_line_id",
        "ach_journal_id",
        "ach_payment_method_line_id",
    )
    def _check_payment_routes(self):
        for route in self:
            if route.cash_journal_id.company_id != route.company_id:
                raise ValidationError(_("The cash journal must belong to the configured company."))
            if route.cash_journal_id.type != "cash":
                raise ValidationError(_("Pay with Cash requires a cash journal."))
            if route.cash_payment_method_line_id not in route.cash_journal_id.inbound_payment_method_line_ids:
                raise ValidationError(_("Select an incoming payment method from the cash journal."))
            if route.ach_journal_id.company_id != route.company_id:
                raise ValidationError(_("The ACH journal must belong to the configured company."))
            if route.ach_journal_id.type != "bank":
                raise ValidationError(_("Pay with ACH requires a bank journal."))
            if route.ach_payment_method_line_id not in route.ach_journal_id.inbound_payment_method_line_ids:
                raise ValidationError(_("Select the configured ACH incoming method from the bank journal."))
