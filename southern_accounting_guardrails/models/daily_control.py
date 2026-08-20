from odoo import _, api, fields, models

from ..accounting_review import (
    BANK_OPEN_WORK_LANES,
    INVOICE_SOURCE_WORK_LANES,
    SOUTHERN_COMPANY_NAME,
)


class SouthernAccountingDailyControl(models.Model):
    _name = "southern.accounting.daily.control"
    _description = "Southern Daily Accounting Control"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "control_date desc, company_id"

    name = fields.Char(compute="_compute_name", store=True)
    control_date = fields.Date(required=True, default=fields.Date.context_today, index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    state = fields.Selection(
        [
            ("open", "Open"),
            ("reviewed", "Reviewed"),
            ("exception", "Exception"),
        ],
        default="open",
        tracking=True,
        index=True,
    )
    bank_line_count = fields.Integer(readonly=True)
    unreconciled_bank_line_count = fields.Integer(readonly=True)
    bank_needs_review_count = fields.Integer(readonly=True)
    bank_exception_count = fields.Integer(readonly=True)
    merchant_batch_needs_review_count = fields.Integer(readonly=True)
    revenue_line_needs_review_count = fields.Integer(readonly=True)
    revenue_line_open_count = fields.Integer(readonly=True)
    product_account_needs_review_count = fields.Integer(readonly=True)
    draft_invoice_count = fields.Integer(readonly=True)
    unverified_migration_invoice_count = fields.Integer(readonly=True)
    pending_coding_candidate_count = fields.Integer(readonly=True)
    bank_blocked_count = fields.Integer(readonly=True)
    bank_merchant_open_count = fields.Integer(readonly=True)
    invoice_source_review_count = fields.Integer(readonly=True)
    product_missing_bucket_count = fields.Integer(readonly=True)
    last_refreshed_at = fields.Datetime(readonly=True)
    review_note = fields.Text(tracking=True)

    _southern_daily_control_unique = models.Constraint(
        "UNIQUE(company_id, control_date)",
        "A daily accounting control already exists for this company and date.",
    )

    @api.depends("control_date", "company_id")
    def _compute_name(self):
        for control in self:
            company = control.company_id.display_name or _("Company")
            date = control.control_date or fields.Date.context_today(control)
            control.name = f"{company} Daily Accounting Control - {date}"

    def action_refresh_counts(self):
        BankLine = self.env["account.bank.statement.line"]
        Move = self.env["account.move"]
        MoveLine = self.env["account.move.line"]
        Batch = self.env["southern.shop_boss.payment.batch"]
        Product = self.env["product.template"]
        Candidate = self.env["southern.bank.coding.candidate"]
        for control in self:
            day_domain = [("company_id", "=", control.company_id.id), ("date", "=", control.control_date)]
            move_day_domain = [
                ("company_id", "=", control.company_id.id),
                ("invoice_date", "=", control.control_date),
                ("move_type", "in", ("out_invoice", "out_refund")),
            ]
            line_day_domain = [
                ("company_id", "=", control.company_id.id),
                ("move_id.invoice_date", "=", control.control_date),
                ("move_id.move_type", "in", ("out_invoice", "out_refund")),
                ("account_id.account_type", "=", "income"),
                ("display_type", "=", False),
            ]
            control.write(
                {
                    "bank_line_count": BankLine.search_count(day_domain),
                    "unreconciled_bank_line_count": BankLine.search_count(day_domain + [("is_reconciled", "=", False)]),
                    "bank_needs_review_count": BankLine.search_count(
                        day_domain + [("southern_review_status", "=", "needs_review")]
                    ),
                    "bank_exception_count": BankLine.search_count(
                        day_domain + [("southern_review_status", "=", "exception")]
                    ),
                    "merchant_batch_needs_review_count": Batch.search_count(
                        [
                            ("company_id", "=", control.company_id.id),
                            ("batch_date", "=", control.control_date),
                            ("review_status", "in", ("draft", "needs_bank_match", "exception")),
                        ]
                    ),
                    "revenue_line_needs_review_count": MoveLine.search_count(
                        line_day_domain + [("southern_revenue_bucket_review", "=", "needs_review")]
                    ),
                    "revenue_line_open_count": MoveLine.search_count(
                        [
                            ("company_id", "=", control.company_id.id),
                            ("move_id.move_type", "in", ("out_invoice", "out_refund")),
                            ("move_id.state", "!=", "cancel"),
                            ("account_id.account_type", "=", "income"),
                            ("display_type", "=", False),
                            ("southern_revenue_bucket_review", "in", ("needs_review", "exception")),
                        ]
                    ),
                    "product_account_needs_review_count": Product.search_count(
                        [
                            "&",
                            "&",
                            ("sale_ok", "=", True),
                            ("company_id", "in", [False, control.company_id.id]),
                            "|",
                            ("southern_income_account_review", "=", "needs_review"),
                            ("southern_expense_account_review", "=", "needs_review"),
                        ]
                    ),
                    "draft_invoice_count": Move.search_count(move_day_domain + [("state", "=", "draft")]),
                    "unverified_migration_invoice_count": Move.search_count(
                        move_day_domain
                        + [
                            ("state", "=", "posted"),
                            ("southern_source_system", "=", "shop_boss"),
                            ("southern_shop_boss_verified", "=", False),
                        ]
                    ),
                    "pending_coding_candidate_count": Candidate.search_count(
                        [
                            ("company_id", "=", control.company_id.id),
                            ("state", "in", ("pending", "approved")),
                        ]
                    ),
                    "bank_blocked_count": BankLine.search_count(
                        [
                            ("company_id", "=", control.company_id.id),
                            ("southern_review_lane", "=", "blocked"),
                        ]
                    ),
                    "bank_merchant_open_count": BankLine.search_count(
                        [
                            ("company_id", "=", control.company_id.id),
                            ("southern_review_lane", "=", "merchant"),
                        ]
                    ),
                    "invoice_source_review_count": Move.search_count(
                        [
                            ("company_id", "=", control.company_id.id),
                            ("move_type", "in", ("out_invoice", "out_refund")),
                            ("state", "!=", "cancel"),
                            ("southern_review_lane", "in", INVOICE_SOURCE_WORK_LANES),
                        ]
                    ),
                    "product_missing_bucket_count": Product.search_count(
                        [
                            ("sale_ok", "=", True),
                            ("company_id", "in", [False, control.company_id.id]),
                            ("southern_accounting_review_lane", "=", "missing_bucket"),
                        ]
                    ),
                    "last_refreshed_at": fields.Datetime.now(),
                }
            )
        return True

    @api.model
    def cron_refresh_daily_controls(self):
        companies = self.env["res.company"].search([("name", "ilike", SOUTHERN_COMPANY_NAME)])
        today = fields.Date.context_today(self)
        for company in companies:
            control = self.search([("company_id", "=", company.id), ("control_date", "=", today)], limit=1)
            if not control:
                control = self.create({"company_id": company.id, "control_date": today})
            control.action_refresh_counts()
        return True

    def action_mark_reviewed(self):
        self.write({"state": "reviewed"})
        return True

    def action_mark_exception(self):
        self.write({"state": "exception"})
        return True

    def action_reopen(self):
        self.write({"state": "open"})
        return True

    def _southern_bank_review_action(self, name, extra_domain):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": name,
            "res_model": "account.bank.statement.line",
            "view_mode": "list,form",
            "search_view_id": self.env.ref(
                "southern_accounting_guardrails.view_southern_bank_statement_line_review_search"
            ).id,
            "views": [
                (
                    self.env.ref(
                        "southern_accounting_guardrails.view_southern_bank_statement_line_review_list"
                    ).id,
                    "list",
                ),
                (
                    self.env.ref(
                        "southern_accounting_guardrails.view_southern_bank_statement_line_review_form"
                    ).id,
                    "form",
                ),
            ],
            "domain": [("company_id", "=", self.company_id.id)] + extra_domain,
        }

    def action_view_bank_review(self):
        return self._southern_bank_review_action(
            _("Daily Bank Work"),
            [
                ("date", "=", self.control_date),
                ("southern_review_lane", "in", BANK_OPEN_WORK_LANES),
            ],
        )

    def action_view_bank_blocked(self):
        return self._southern_bank_review_action(
            _("Blocked Bank Exceptions"),
            [("southern_review_lane", "=", "blocked")],
        )

    def action_view_bank_merchant(self):
        return self._southern_bank_review_action(
            _("Open Merchant Settlements"),
            [("southern_review_lane", "=", "merchant")],
        )

    def action_view_bank_coding_candidates(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Pending Bank Coding"),
            "res_model": "southern.bank.coding.candidate",
            "view_mode": "list,form",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("state", "in", ("pending", "approved")),
            ],
        }

    def action_view_invoice_source_review(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Invoice Source Review"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("move_type", "in", ("out_invoice", "out_refund")),
                ("state", "!=", "cancel"),
                ("southern_review_lane", "in", INVOICE_SOURCE_WORK_LANES),
            ],
        }

    def action_view_revenue_review(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Revenue Bucket Review"),
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("move_id.move_type", "in", ("out_invoice", "out_refund")),
                ("move_id.state", "!=", "cancel"),
                ("account_id.account_type", "=", "income"),
                ("display_type", "=", False),
                ("southern_revenue_bucket_review", "in", ("needs_review", "exception")),
            ],
        }

    def action_view_product_account_review(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Product Accounting Review"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [
                ("sale_ok", "=", True),
                ("company_id", "in", [False, self.company_id.id]),
                ("southern_accounting_review_lane", "!=", "ok"),
            ],
        }

    def action_view_product_missing_bucket(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Missing Revenue Buckets"),
            "res_model": "product.template",
            "view_mode": "list,form",
            "domain": [
                ("sale_ok", "=", True),
                ("company_id", "in", [False, self.company_id.id]),
                ("southern_accounting_review_lane", "=", "missing_bucket"),
            ],
        }
