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
    processing_fee_enabled = fields.Boolean(
        string="Apply Stripe Terminal Processing Fee",
        default=False,
        help="Adds the processing fee only when an invoice's Payment Type is Stripe Terminal.",
    )
    processing_fee_percentage = fields.Float(
        string="Processing Fee Percentage",
        default=3.5,
        digits=(16, 4),
    )
    processing_fee_fixed = fields.Monetary(
        string="Fixed Processing Fee",
        default=0.30,
        currency_field="currency_id",
    )
    processing_fee_name = fields.Char(
        string="Processing Fee Description",
        default="Transaction Processing Fee",
        required=True,
    )
    processing_fee_income_account_id = fields.Many2one(
        "account.account",
        string="Processing Fee Income Account",
        check_company=True,
        domain="[('account_type', 'in', ['income', 'income_other']), ('company_ids', 'in', company_id)]",
        help="Revenue account used for the separately itemized Stripe Terminal processing fee.",
    )
    processing_fee_tax_ids = fields.Many2many(
        "account.tax",
        string="Processing Fee Taxes",
        domain="[('type_tax_use', '=', 'sale'), ('company_id', '=', company_id)]",
        check_company=True,
        help="Leave empty when the fee should be added after sales tax. Configure only after tax review.",
    )
    currency_id = fields.Many2one(related="company_id.currency_id")

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

    @api.constrains(
        "processing_fee_enabled",
        "processing_fee_percentage",
        "processing_fee_fixed",
        "processing_fee_income_account_id",
    )
    def _check_processing_fee_configuration(self):
        for route in self:
            if route.processing_fee_percentage < 0 or route.processing_fee_percentage >= 100:
                raise ValidationError(_("The processing fee percentage must be at least 0 and below 100."))
            if route.processing_fee_fixed < 0:
                raise ValidationError(_("The fixed processing fee cannot be negative."))
            if route.processing_fee_enabled and not route.processing_fee_income_account_id:
                raise ValidationError(_("Select a processing fee income account before enabling the fee."))
