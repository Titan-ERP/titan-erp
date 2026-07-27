from odoo import api, fields, models


class SouthernAccountingPolicy(models.Model):
    _name = "southern.accounting.policy"
    _description = "Southern Accounting Policy"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "company_id"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)
    parts_revenue_account_id = fields.Many2one(
        "account.account",
        domain="[('account_type', '=', 'income')]",
        tracking=True,
    )
    service_revenue_account_id = fields.Many2one(
        "account.account",
        domain="[('account_type', '=', 'income')]",
        tracking=True,
    )
    rental_revenue_account_id = fields.Many2one(
        "account.account",
        domain="[('account_type', '=', 'income')]",
        tracking=True,
    )
    equipment_revenue_account_id = fields.Many2one(
        "account.account",
        domain="[('account_type', '=', 'income')]",
        tracking=True,
    )
    fees_revenue_account_id = fields.Many2one(
        "account.account",
        domain="[('account_type', '=', 'income')]",
        tracking=True,
    )
    merchant_fee_account_id = fields.Many2one(
        "account.account",
        string="Merchant Fee Expense Account",
        domain="[('account_type', 'in', ['expense', 'expense_direct_cost'])]",
        tracking=True,
    )
    payment_clearing_account_id = fields.Many2one(
        "account.account",
        string="Payment Clearing Account",
        domain="[('account_type', 'in', ['asset_current', 'asset_cash'])]",
        tracking=True,
    )
    check_review_account_id = fields.Many2one(
        "account.account",
        string="Checks Pending Review Account",
        domain="[('account_type', 'in', ['expense', 'expense_direct_cost'])]",
        tracking=True,
    )
    auto_apply_draft_revenue_accounts = fields.Boolean(
        string="Auto-Apply Draft Revenue Accounts",
        help="Allows draft invoice lines to be routed to the configured account by user action or future automation.",
    )
    require_product_bucket = fields.Boolean(
        string="Require Product Revenue Bucket",
        default=True,
        help="Flags saleable products without a Southern revenue bucket for accounting setup review.",
    )
    merchant_fee_tolerance = fields.Monetary(default=2.0)
    bank_match_tolerance = fields.Monetary(default=1.0)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    note = fields.Text()

    _southern_accounting_policy_company_unique = models.Constraint(
        "UNIQUE(company_id)",
        "Only one Southern accounting policy can be active per company.",
    )

    @api.depends("company_id")
    def _compute_name(self):
        for policy in self:
            company = policy.company_id.display_name or "Company"
            policy.name = f"{company} Accounting Policy"

    @api.model
    def find_company_policy(self, company):
        company = company or self.env.company
        return self.search([("company_id", "=", company.id), ("active", "=", True)], limit=1)

    @api.model
    def get_company_policy(self, company):
        company = company or self.env.company
        policy = self.find_company_policy(company)
        if not policy:
            policy = self.create({"company_id": company.id})
            policy.action_fill_from_chart()
        return policy

    def get_revenue_account(self, bucket):
        self.ensure_one()
        return {
            "parts": self.parts_revenue_account_id,
            "service": self.service_revenue_account_id,
            "rental": self.rental_revenue_account_id,
            "equipment": self.equipment_revenue_account_id,
            "fees": self.fees_revenue_account_id,
        }.get(bucket, self.env["account.account"])

    def action_fill_from_chart(self):
        Account = self.env["account.account"]
        codes = {
            "parts_revenue_account_id": "410000",
            "service_revenue_account_id": "420000",
            "rental_revenue_account_id": "430000",
            "payment_clearing_account_id": "109998",
            "check_review_account_id": "699998",
        }
        names = {
            "parts_revenue_account_id": "Parts Revenue",
            "service_revenue_account_id": "Service Revenue",
            "rental_revenue_account_id": "Rental Revenue",
            "merchant_fee_account_id": "Bank Merchant Fees",
            "payment_clearing_account_id": "Shop Boss Payment Clearing",
            "check_review_account_id": "Checks Pending Payee Review",
        }
        for policy in self:
            vals = {}
            for field_name, code in codes.items():
                if policy[field_name]:
                    continue
                account = Account.search(
                    [("company_ids", "in", policy.company_id.id), ("code", "=", code)],
                    limit=1,
                )
                if not account:
                    account = Account.search([("code", "=", code)], limit=1)
                if account:
                    vals[field_name] = account.id
            for field_name, name in names.items():
                if policy[field_name] or field_name in vals:
                    continue
                account = Account.search(
                    [
                        ("company_ids", "in", policy.company_id.id),
                        ("name", "=", name),
                    ],
                    limit=1,
                )
                if account:
                    vals[field_name] = account.id
            if vals:
                policy.write(vals)
        return True
