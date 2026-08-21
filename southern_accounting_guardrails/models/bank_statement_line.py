from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..accounting_review import classify_bank_review
from .bank_review_logic import (
    CHECK_RE,
    MERCHANT_RE,
    classify_review_bucket,
    payroll_direct_expense_risk,
    settlement_direct_revenue_risk,
)


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
            ("shop_boss_payment", "Customer / Legacy Payment"),
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
            ("shop_boss_payment", "Customer / Legacy Payment"),
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
        string="Legacy Source Document",
        index=True,
    )
    southern_shop_boss_payment_batch_id = fields.Many2one(
        "southern.shop_boss.payment.batch",
        string="Legacy Payment Batch",
        index=True,
    )
    southern_shop_boss_batch_ref = fields.Char(string="Legacy Batch / Payment Ref", index=True)
    southern_expected_merchant_fee = fields.Monetary(
        string="Expected Merchant Fee",
        currency_field="currency_id",
        help="Optional expected fee for gross-to-net legacy/card batch settlement review.",
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
    southern_payroll_direct_expense = fields.Boolean(
        string="Payroll Direct-Expense Risk",
        compute="_compute_southern_coding_risks",
        store=True,
        index=True,
        help="Payroll withdrawal is reconciled directly to an expense instead of a posted payroll liability.",
    )
    southern_settlement_direct_revenue = fields.Boolean(
        string="Settlement Direct-Revenue Risk",
        compute="_compute_southern_coding_risks",
        store=True,
        index=True,
        help="Merchant settlement is reconciled directly to revenue instead of a processor clearing batch.",
    )
    southern_review_lane = fields.Selection(
        [
            ("blocked", "Blocked Exception"),
            ("merchant", "Merchant Settlement"),
            ("payroll", "Payroll"),
            ("check_payee", "Check Payee"),
            ("missing_partner", "Missing Partner"),
            ("ordinary", "Ordinary Review"),
            ("reviewed", "Reviewed"),
            ("not_required", "Not Required"),
        ],
        string="Southern Review Lane",
        compute="_compute_southern_review_lane",
        store=True,
        index=True,
    )
    southern_review_details = fields.Char(
        string="Southern Review Details",
        compute="_compute_southern_review_lane",
        store=True,
    )

    @api.depends(
        "payment_ref",
        "amount",
        "partner_id",
        "partner_id.supplier_rank",
        "southern_manual_review_bucket",
    )
    def _compute_southern_review_flags(self):
        for line in self:
            ref = line.payment_ref or ""
            line.southern_is_merchant_settlement = bool(MERCHANT_RE.search(ref)) and line.amount > 0
            line.southern_is_generic_check = bool(CHECK_RE.search(ref)) and not line.partner_id
            line.southern_missing_partner = not bool(line.partner_id)
            line.southern_review_bucket = classify_review_bucket(
                ref,
                line.amount,
                has_partner=bool(line.partner_id),
                supplier_rank=line.partner_id.supplier_rank,
                manual_bucket=line.southern_manual_review_bucket,
            )

    @api.depends(
        "payment_ref",
        "amount",
        "move_id.line_ids.account_id",
        "move_id.line_ids.account_id.account_type",
    )
    def _compute_southern_coding_risks(self):
        for line in self:
            account_types = set(line.move_id.line_ids.account_id.mapped("account_type"))
            line.southern_payroll_direct_expense = payroll_direct_expense_risk(
                line.payment_ref,
                line.amount,
                account_types,
            )
            line.southern_settlement_direct_revenue = settlement_direct_revenue_risk(
                line.payment_ref,
                line.amount,
                account_types,
            )

    @api.depends(
        "southern_review_status",
        "is_reconciled",
        "southern_is_merchant_settlement",
        "southern_settlement_direct_revenue",
        "southern_payroll_direct_expense",
        "southern_review_bucket",
        "southern_is_generic_check",
        "southern_missing_partner",
    )
    def _compute_southern_review_lane(self):
        for line in self:
            lane, details = classify_bank_review(
                line.southern_review_status,
                is_reconciled=line.is_reconciled,
                is_merchant_settlement=line.southern_is_merchant_settlement,
                settlement_direct_revenue=line.southern_settlement_direct_revenue,
                payroll_direct_expense=line.southern_payroll_direct_expense,
                review_bucket=line.southern_review_bucket,
                is_generic_check=line.southern_is_generic_check,
                missing_partner=line.southern_missing_partner,
            )
            line.southern_review_lane = lane
            line.southern_review_details = details

    def action_southern_mark_reviewed(self):
        for line in self:
            if line.southern_review_bucket == "payroll" and not line.is_reconciled:
                raise UserError(
                    _("Reconcile payroll withdrawals to the posted payroll liability before marking them reviewed.")
                )
            if line.southern_payroll_direct_expense:
                raise UserError(
                    _("Payroll withdrawals cannot be approved when coded directly to expense; use payroll liabilities.")
                )
            if line.southern_is_merchant_settlement and not line.is_reconciled:
                raise UserError(
                    _("Match the merchant settlement to its processor clearing batch before marking it reviewed.")
                )
            if line.southern_settlement_direct_revenue:
                raise UserError(
                    _("Merchant settlements cannot be approved when coded directly to revenue; use clearing.")
                )
        self.write({"southern_review_status": "reviewed"})

    def action_southern_mark_exception(self):
        self.write({"southern_review_status": "exception"})

    def action_southern_mark_needs_review(self):
        self.write({"southern_review_status": "needs_review"})

    def action_southern_clear_manual_bucket(self):
        self.write({"southern_manual_review_bucket": False})
