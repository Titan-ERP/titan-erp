from odoo import fields, models


class SouthernAccountingRevenueRule(models.Model):
    _name = "southern.accounting.revenue.rule"
    _description = "Southern Revenue Classification Rule"
    _order = "sequence, name"

    name = fields.Char(required=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    match_text = fields.Char(
        required=True,
        help="Case-insensitive text to find in the invoice line, product, category, partner, or current account.",
    )
    revenue_bucket = fields.Selection(
        [
            ("parts", "Parts"),
            ("service", "Service"),
            ("rental", "Rental"),
            ("freight", "Freight"),
            ("equipment", "Equipment / Asset Sale"),
            ("fees", "Fees"),
            ("other", "Other"),
        ],
        required=True,
    )
    income_account_id = fields.Many2one(
        "account.account",
        string="Preferred Income Account",
        domain="[('account_type', '=', 'income')]",
    )
    note = fields.Text()

    def matches_move_line(self, line):
        self.ensure_one()
        if self.company_id and line.company_id and self.company_id != line.company_id:
            return False
        needle = (self.match_text or "").casefold()
        if not needle:
            return False
        haystack = " ".join(
            value
            for value in (
                line.name or "",
                line.product_id.display_name or "",
                line.product_id.categ_id.complete_name or line.product_id.categ_id.name or "",
                line.partner_id.display_name or "",
                line.account_id.display_name or "",
            )
            if value
        ).casefold()
        return needle in haystack
