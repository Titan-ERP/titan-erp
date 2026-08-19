from odoo import api, fields, models

from ..accounting_review import classify_product_accounting_review


class ProductCategory(models.Model):
    _inherit = "product.category"

    southern_accounting_bucket = fields.Selection(
        [
            ("parts", "Parts"),
            ("service", "Service"),
            ("rental", "Rental"),
            ("freight", "Freight"),
            ("equipment", "Equipment"),
            ("fees", "Fees"),
            ("other", "Other"),
        ],
        string="Southern Accounting Bucket",
        index=True,
        help="Expected accounting bucket for reporting and product/category account audits.",
    )
    southern_accounting_review_note = fields.Text(string="Accounting Review Note")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    southern_revenue_bucket = fields.Selection(
        [
            ("parts", "Parts Revenue"),
            ("service", "Service Revenue"),
            ("rental", "Rental Revenue"),
            ("freight", "Shipping / Freight Revenue"),
            ("equipment", "Equipment / Asset Sale"),
            ("fees", "Fees"),
            ("other", "Other"),
        ],
        string="Southern Revenue Bucket",
        index=True,
        help="Expected revenue reporting bucket when this product appears on an invoice or order.",
    )
    southern_accounting_review_note = fields.Text(string="Accounting Review Note")
    southern_expected_income_account_id = fields.Many2one(
        "account.account",
        string="Expected Southern Income Account",
        compute="_compute_southern_expected_income_account",
    )
    southern_expected_expense_account_id = fields.Many2one(
        "account.account",
        string="Expected Southern Cost Account",
        compute="_compute_southern_expected_expense_account",
    )
    southern_income_account_review = fields.Selection(
        [
            ("ok", "OK"),
            ("needs_review", "Needs Review"),
        ],
        string="Southern Income Account Review",
        compute="_compute_southern_income_account_review",
        store=True,
        index=True,
    )
    southern_expense_account_review = fields.Selection(
        [
            ("ok", "OK"),
            ("needs_review", "Needs Review"),
            ("not_required", "Not Required"),
        ],
        string="Southern Cost Account Review",
        compute="_compute_southern_expense_account_review",
        store=True,
        index=True,
    )
    southern_accounting_review_lane = fields.Selection(
        [
            ("ok", "OK"),
            ("missing_bucket", "Missing Revenue Bucket"),
            ("income", "Income Account"),
            ("cost", "Cost Account"),
            ("both", "Income and Cost"),
        ],
        string="Southern Accounting Review Lane",
        compute="_compute_southern_accounting_review_lane",
        store=True,
        index=True,
    )
    southern_accounting_review_details = fields.Char(
        string="Southern Accounting Review Details",
        compute="_compute_southern_accounting_review_lane",
        store=True,
    )

    @api.depends("southern_revenue_bucket", "company_id")
    def _compute_southern_expected_income_account(self):
        Policy = self.env["southern.accounting.policy"]
        for template in self:
            company = template.company_id or self.env.company
            policy = Policy.find_company_policy(company)
            template.southern_expected_income_account_id = (
                policy.get_revenue_account(template.southern_revenue_bucket) if policy else False
            )

    @api.depends("southern_revenue_bucket", "company_id")
    def _compute_southern_expected_expense_account(self):
        Policy = self.env["southern.accounting.policy"]
        for template in self:
            company = template.company_id or self.env.company
            policy = Policy.find_company_policy(company)
            template.southern_expected_expense_account_id = (
                policy.get_cost_account(template.southern_revenue_bucket) if policy else False
            )

    @api.depends(
        "sale_ok",
        "southern_revenue_bucket",
        "company_id",
        "property_account_income_id",
        "property_account_income_id.code",
        "categ_id.property_account_income_categ_id",
        "categ_id.property_account_income_categ_id.code",
    )
    def _compute_southern_income_account_review(self):
        expected_prefix = {
            "parts": "410",
            "service": "420",
            "rental": "430",
        }
        Policy = self.env["southern.accounting.policy"]
        for template in self:
            company = template.company_id or self.env.company
            policy = Policy.find_company_policy(company)
            require_bucket = policy.require_product_bucket if policy else True
            if template.sale_ok and require_bucket and not template.southern_revenue_bucket:
                template.southern_income_account_review = "needs_review"
                continue
            prefix = expected_prefix.get(template.southern_revenue_bucket)
            account = template.property_account_income_id or template.categ_id.property_account_income_categ_id
            code = account.code or ""
            if (
                template.southern_expected_income_account_id
                and account
                and account != template.southern_expected_income_account_id
            ):
                template.southern_income_account_review = "needs_review"
            elif prefix and account and code and not code.startswith(prefix):
                template.southern_income_account_review = "needs_review"
            else:
                template.southern_income_account_review = "ok"

    @api.depends(
        "southern_revenue_bucket",
        "company_id",
        "property_account_expense_id",
        "property_account_expense_id.code",
        "categ_id.property_account_expense_categ_id",
        "categ_id.property_account_expense_categ_id.code",
    )
    def _compute_southern_expense_account_review(self):
        expected_prefix = {
            "equipment": "500",
            "parts": "510",
            "service": "520",
            "rental": "530",
        }
        for template in self:
            prefix = expected_prefix.get(template.southern_revenue_bucket)
            if not prefix:
                template.southern_expense_account_review = "not_required"
                continue
            account = template.property_account_expense_id or template.categ_id.property_account_expense_categ_id
            code = account.code if account else ""
            if not account:
                template.southern_expense_account_review = "needs_review"
            elif (
                template.southern_expected_expense_account_id
                and account != template.southern_expected_expense_account_id
            ):
                template.southern_expense_account_review = "needs_review"
            elif code and not code.startswith(prefix):
                template.southern_expense_account_review = "needs_review"
            else:
                template.southern_expense_account_review = "ok"

    @api.depends(
        "sale_ok",
        "southern_revenue_bucket",
        "southern_income_account_review",
        "southern_expense_account_review",
        "southern_expected_income_account_id",
        "southern_expected_expense_account_id",
        "company_id",
    )
    def _compute_southern_accounting_review_lane(self):
        Policy = self.env["southern.accounting.policy"]
        for template in self:
            company = template.company_id or self.env.company
            policy = Policy.find_company_policy(company)
            require_bucket = policy.require_product_bucket if policy else True
            lane, details = classify_product_accounting_review(
                template.sale_ok,
                template.southern_revenue_bucket,
                template.southern_income_account_review,
                template.southern_expense_account_review,
                require_product_bucket=require_bucket,
                expected_income_name=template.southern_expected_income_account_id.display_name,
                expected_expense_name=template.southern_expected_expense_account_id.display_name,
            )
            template.southern_accounting_review_lane = lane
            template.southern_accounting_review_details = details
