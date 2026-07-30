import re

from odoo import api, fields, models


RENTAL_RE = re.compile(r"\b(TX60|TX18|TX10|U35)\b|RENTAL|RENT\b", re.I)
SERVICE_RE = re.compile(r"SERVICE|LABOR|REPAIR|DIAG|DIAGNOSTIC|SHOP SUPPL", re.I)
PARTS_RE = re.compile(r"PART|FILTER|SEAL|HOSE|BOLT|BLADE|TOOTH|CUTTING EDGE", re.I)


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    southern_revenue_bucket = fields.Selection(
        [
            ("parts", "Parts"),
            ("service", "Service"),
            ("rental", "Rental"),
            ("equipment", "Equipment / Asset Sale"),
            ("fees", "Fees"),
            ("other", "Other"),
        ],
        string="Southern Revenue Bucket",
        compute="_compute_southern_revenue_bucket",
        store=True,
        index=True,
    )
    southern_manual_revenue_bucket = fields.Selection(
        [
            ("parts", "Parts"),
            ("service", "Service"),
            ("rental", "Rental"),
            ("equipment", "Equipment / Asset Sale"),
            ("fees", "Fees"),
            ("other", "Other"),
        ],
        string="Manual Southern Revenue Bucket",
        index=True,
        copy=False,
        help="Optional override when the native rule/category inference is not specific enough.",
    )
    southern_expected_income_account_id = fields.Many2one(
        "account.account",
        string="Expected Southern Income Account",
        compute="_compute_southern_expected_income_account",
    )
    southern_revenue_bucket_review = fields.Selection(
        [
            ("ok", "OK"),
            ("needs_review", "Needs Review"),
            ("exception", "Exception"),
        ],
        string="Southern Revenue Review",
        compute="_compute_southern_revenue_bucket_review",
        store=True,
        index=True,
    )
    southern_revenue_review_override = fields.Selection(
        [
            ("accepted", "Accepted"),
            ("exception", "Exception"),
        ],
        string="Southern Review Override",
        copy=False,
        index=True,
    )
    southern_revenue_manual_note = fields.Char(string="Southern Manual Revenue Note", copy=False)
    southern_revenue_bucket_note = fields.Char(
        string="Southern Revenue Note",
        compute="_compute_southern_revenue_bucket_review",
        store=True,
    )

    @api.depends(
        "name",
        "account_id",
        "account_id.code",
        "southern_manual_revenue_bucket",
        "product_id",
        "product_id.product_tmpl_id.southern_revenue_bucket",
        "product_id.categ_id.southern_accounting_bucket",
    )
    def _compute_southern_revenue_bucket(self):
        Rule = self.env["southern.accounting.revenue.rule"]
        active_rules = Rule.search([("active", "=", True)], order="sequence, id")
        for line in self:
            bucket = False
            if not line._southern_is_customer_revenue_line():
                line.southern_revenue_bucket = False
                continue
            if line.southern_manual_revenue_bucket:
                line.southern_revenue_bucket = line.southern_manual_revenue_bucket
                continue
            product_bucket = line.product_id.product_tmpl_id.southern_revenue_bucket
            category_bucket = line.product_id.categ_id.southern_accounting_bucket
            if product_bucket and product_bucket != "other":
                bucket = product_bucket
            elif category_bucket and category_bucket != "other":
                bucket = category_bucket
            if not bucket:
                matched_rule = active_rules.filtered(lambda rule: rule.matches_move_line(line))[:1]
                bucket = matched_rule.revenue_bucket if matched_rule else False
            if not bucket:
                bucket = line._southern_guess_revenue_bucket()
            line.southern_revenue_bucket = bucket or "other"

    def _southern_is_customer_revenue_line(self):
        self.ensure_one()
        return (
            self.move_id.move_type in ("out_invoice", "out_refund")
            and not self.display_type
            and self.account_id.account_type == "income"
        )

    def _southern_guess_revenue_bucket(self):
        self.ensure_one()
        text = " ".join(
            value
            for value in (
                self.name or "",
                self.product_id.display_name or "",
                self.account_id.name or "",
                self.account_id.code or "",
            )
            if value
        )
        code = self.account_id.code or ""
        if code.startswith("430") or RENTAL_RE.search(text):
            return "rental"
        if code.startswith("420") or SERVICE_RE.search(text):
            return "service"
        if code.startswith("410") or PARTS_RE.search(text):
            return "parts"
        if code.startswith("44") or "EQUIPMENT" in text.upper():
            return "equipment"
        return "other"

    @api.depends("southern_revenue_bucket", "company_id")
    def _compute_southern_expected_income_account(self):
        code_by_bucket = {
            "parts": "410000",
            "service": "420000",
            "rental": "430000",
        }
        Account = self.env["account.account"]
        for line in self:
            code = code_by_bucket.get(line.southern_revenue_bucket)
            account = Account.browse()
            if code:
                account = Account.search(
                    [("company_ids", "in", line.company_id.id), ("code", "=", code)],
                    limit=1,
                )
                if not account:
                    account = Account.search([("code", "=", code)], limit=1)
            line.southern_expected_income_account_id = account

    @api.depends(
        "account_id",
        "account_id.code",
        "southern_revenue_bucket",
        "move_id.state",
        "southern_revenue_review_override",
        "southern_revenue_manual_note",
    )
    def _compute_southern_revenue_bucket_review(self):
        expected_prefix = {
            "parts": "410",
            "service": "420",
            "rental": "430",
        }
        for line in self:
            if line.southern_revenue_review_override == "accepted":
                line.southern_revenue_bucket_review = "ok"
                line.southern_revenue_bucket_note = line.southern_revenue_manual_note or "Accepted by accounting review."
                continue
            if line.southern_revenue_review_override == "exception":
                line.southern_revenue_bucket_review = "exception"
                line.southern_revenue_bucket_note = line.southern_revenue_manual_note or "Marked as accounting exception."
                continue
            if not line._southern_is_customer_revenue_line():
                line.southern_revenue_bucket_review = "ok"
                line.southern_revenue_bucket_note = False
                continue
            prefix = expected_prefix.get(line.southern_revenue_bucket)
            code = line.account_id.code or ""
            if prefix and not code.startswith(prefix):
                line.southern_revenue_bucket_review = "needs_review"
                line.southern_revenue_bucket_note = (
                    f"Expected {line.southern_revenue_bucket} revenue account; currently {code or line.account_id.display_name}."
                )
            elif line.southern_revenue_bucket == "other":
                line.southern_revenue_bucket_review = "needs_review"
                line.southern_revenue_bucket_note = "Revenue bucket could not be classified natively."
            else:
                line.southern_revenue_bucket_review = "ok"
                line.southern_revenue_bucket_note = False

    def action_southern_open_revenue_lines(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Revenue Bucket Review",
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "domain": [("id", "in", self.ids)],
        }

    def action_southern_accept_revenue_bucket(self):
        self.write({"southern_revenue_review_override": "accepted"})

    def action_southern_mark_revenue_exception(self):
        self.write({"southern_revenue_review_override": "exception"})

    def action_southern_clear_revenue_review_override(self):
        self.write({"southern_revenue_review_override": False, "southern_revenue_manual_note": False})

    def action_southern_apply_expected_income_account(self):
        for line in self:
            if (
                line.move_id.state != "draft"
                or not line._southern_is_customer_revenue_line()
                or not line.southern_expected_income_account_id
            ):
                continue
            line.account_id = line.southern_expected_income_account_id
