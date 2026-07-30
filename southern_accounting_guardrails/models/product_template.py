from odoo import api, fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    southern_accounting_bucket = fields.Selection(
        [
            ("parts", "Parts"),
            ("service", "Service"),
            ("rental", "Rental"),
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

    @api.depends("southern_revenue_bucket", "company_id")
    def _compute_southern_expected_income_account(self):
        code_by_bucket = {
            "parts": "410000",
            "service": "420000",
            "rental": "430000",
        }
        Account = self.env["account.account"]
        for template in self:
            code = code_by_bucket.get(template.southern_revenue_bucket)
            account = Account.browse()
            if code:
                company = template.company_id or self.env.company
                account = Account.search([("company_ids", "in", company.id), ("code", "=", code)], limit=1)
                if not account:
                    account = Account.search([("code", "=", code)], limit=1)
            template.southern_expected_income_account_id = account

    @api.depends(
        "southern_revenue_bucket",
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
        for template in self:
            prefix = expected_prefix.get(template.southern_revenue_bucket)
            account = template.property_account_income_id or template.categ_id.property_account_income_categ_id
            code = account.code or ""
            template.southern_income_account_review = (
                "needs_review" if prefix and account and not code.startswith(prefix) else "ok"
            )
