from odoo import api, fields, models


class SouthernShopBossPaymentBatch(models.Model):
    _name = "southern.shop_boss.payment.batch"
    _description = "Shop Boss Payment / Merchant Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "batch_date desc, name desc"

    name = fields.Char(required=True, index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    batch_date = fields.Date(required=True, index=True, tracking=True)
    payment_source = fields.Selection(
        [
            ("cash", "Cash"),
            ("check", "Check"),
            ("card", "Card"),
            ("merchant", "Merchant Settlement"),
            ("ach", "ACH"),
            ("other", "Other"),
        ],
        default="merchant",
        required=True,
        index=True,
        tracking=True,
    )
    shop_boss_reference = fields.Char(index=True)
    document_ids = fields.One2many(
        "southern.shop_boss.document",
        "payment_batch_id",
        string="Shop Boss Documents",
    )
    bank_statement_line_ids = fields.One2many(
        "account.bank.statement.line",
        "southern_shop_boss_payment_batch_id",
        string="Bank Lines",
    )
    gross_amount = fields.Monetary(default=0.0, tracking=True)
    merchant_fee_amount = fields.Monetary(default=0.0, tracking=True)
    net_expected_amount = fields.Monetary(compute="_compute_amounts", store=True)
    bank_total_amount = fields.Monetary(compute="_compute_amounts", store=True)
    variance_amount = fields.Monetary(compute="_compute_amounts", store=True)
    review_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("needs_bank_match", "Needs Bank Match"),
            ("matched", "Matched"),
            ("exception", "Exception"),
        ],
        default="draft",
        index=True,
        tracking=True,
    )
    review_note = fields.Text(tracking=True)

    @api.depends("gross_amount", "merchant_fee_amount", "bank_statement_line_ids.amount")
    def _compute_amounts(self):
        for batch in self:
            batch.net_expected_amount = batch.gross_amount - batch.merchant_fee_amount
            batch.bank_total_amount = sum(batch.bank_statement_line_ids.mapped("amount"))
            batch.variance_amount = batch.bank_total_amount - batch.net_expected_amount

    def action_mark_needs_bank_match(self):
        self.write({"review_status": "needs_bank_match"})

    def action_mark_matched(self):
        self.write({"review_status": "matched"})

    def action_mark_exception(self):
        self.write({"review_status": "exception"})
