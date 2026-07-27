import re

from odoo import api, fields, models


MERCHANT_RE = re.compile(r"BANKCARD|MERCHANT|MTOT DEP|NET SETTLE", re.I)
CHECK_RE = re.compile(r"\bCHECK\b|INTUIT.*/CHECKS", re.I)


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    southern_review_status = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("needs_review", "Needs Review"),
            ("reviewed", "Reviewed"),
            ("exception", "Exception"),
        ],
        string="Southern Review",
        default="needs_review",
        index=True,
    )
    southern_review_bucket = fields.Selection(
        [
            ("shop_boss_payment", "Shop Boss Payment"),
            ("merchant_fee", "Merchant Fee / Net Deposit"),
            ("check_payee", "Check Payee"),
            ("loan", "Loan / Note"),
            ("tax", "Tax"),
            ("payroll", "Payroll"),
            ("vendor", "Vendor Expense"),
            ("other", "Other"),
        ],
        string="Southern Bucket",
        compute="_compute_southern_review_flags",
        store=True,
        index=True,
    )
    southern_manual_review_bucket = fields.Selection(
        [
            ("shop_boss_payment", "Migration Payment"),
            ("merchant_fee", "Merchant Fee / Net Deposit"),
            ("check_payee", "Check Payee"),
            ("loan", "Loan / Note"),
            ("tax", "Tax"),
            ("payroll", "Payroll"),
            ("vendor", "Vendor Expense"),
            ("other", "Other"),
        ],
        string="Manual Southern Bucket",
        index=True,
        help="Optional override for bank-line review classification.",
    )
    southern_review_note = fields.Text(string="Southern Review Note")
    southern_shop_boss_document_id = fields.Many2one(
        "southern.shop_boss.document",
        string="Shop Boss Document",
        index=True,
    )
    southern_shop_boss_payment_batch_id = fields.Many2one(
        "southern.shop_boss.payment.batch",
        string="Shop Boss Payment Batch",
        index=True,
    )
    southern_shop_boss_batch_ref = fields.Char(string="Shop Boss Batch / Payment Ref", index=True)
    southern_expected_merchant_fee = fields.Monetary(
        string="Expected Merchant Fee",
        currency_field="currency_id",
        help="Optional expected fee for gross-to-net Shop Boss/card batch settlement review.",
    )
    southern_is_merchant_settlement = fields.Boolean(
        string="Merchant Settlement",
        compute="_compute_southern_review_flags",
        store=True,
        index=True,
    )
    southern_missing_partner = fields.Boolean(
        string="Missing Partner",
        compute="_compute_southern_review_flags",
        store=True,
        index=True,
    )
    southern_is_generic_check = fields.Boolean(
        string="Generic Check",
        compute="_compute_southern_review_flags",
        store=True,
        index=True,
    )

    @api.depends("payment_ref", "amount", "partner_id", "southern_manual_review_bucket")
    def _compute_southern_review_flags(self):
        for line in self:
            ref = line.payment_ref or ""
            line.southern_is_merchant_settlement = bool(MERCHANT_RE.search(ref)) and line.amount > 0
            line.southern_is_generic_check = bool(CHECK_RE.search(ref)) and not line.partner_id
            line.southern_missing_partner = not bool(line.partner_id)
            if line.southern_manual_review_bucket:
                line.southern_review_bucket = line.southern_manual_review_bucket
            elif line.southern_is_merchant_settlement:
                line.southern_review_bucket = "merchant_fee"
            elif line.southern_is_generic_check:
                line.southern_review_bucket = "check_payee"
            elif "LOAN" in ref.upper() or " LN " in ref.upper():
                line.southern_review_bucket = "loan"
            elif "IRS" in ref.upper() or "TAX" in ref.upper():
                line.southern_review_bucket = "tax"
            elif "PAYROLL" in ref.upper() or "INTUIT" in ref.upper():
                line.southern_review_bucket = "payroll"
            elif line.amount > 0:
                line.southern_review_bucket = "shop_boss_payment"
            else:
                line.southern_review_bucket = "vendor"

    def action_southern_mark_reviewed(self):
        self.write({"southern_review_status": "reviewed"})

    def action_southern_mark_exception(self):
        self.write({"southern_review_status": "exception"})

    def action_southern_mark_needs_review(self):
        self.write({"southern_review_status": "needs_review"})

    def action_southern_clear_manual_bucket(self):
        self.write({"southern_manual_review_bucket": False})
