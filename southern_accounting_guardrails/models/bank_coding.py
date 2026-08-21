import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..accounting_review import SOUTHERN_COMPANY_NAME
from .merchant_guard import is_unsafe_merchant_target


class SouthernBankCodingRule(models.Model):
    _name = "southern.bank.coding.rule"
    _description = "Southern Bank Coding Rule"
    _inherit = ["mail.thread"]
    _order = "sequence, id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True)
    sequence = fields.Integer(default=10)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("approved", "Approved"),
            ("retired", "Retired"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    match_pattern = fields.Char(
        required=True,
        help="Case-insensitive regular expression matched against the bank line label/reference.",
    )
    amount_direction = fields.Selection(
        [("any", "Any"), ("debit", "Money Out"), ("credit", "Money In")],
        default="any",
        required=True,
    )
    minimum_amount = fields.Monetary()
    maximum_amount = fields.Monetary()
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", store=True, readonly=True
    )
    target_account_id = fields.Many2one(
        "account.account", tracking=True, ondelete="restrict"
    )
    target_account_name = fields.Char(
        help="Bootstrap/account-review hint retained when an exact account cannot be resolved."
    )
    effective_from = fields.Date()
    effective_to = fields.Date()
    approved_by_id = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)
    note = fields.Text()
    match_count = fields.Integer(readonly=True)
    last_matched_at = fields.Datetime(readonly=True)

    @api.constrains("match_pattern")
    def _check_pattern(self):
        for rule in self:
            try:
                re.compile(rule.match_pattern or "", re.IGNORECASE)
            except re.error as error:
                raise ValidationError(_("Invalid regular expression: %s") % error) from error

    def action_approve(self):
        for rule in self:
            if not rule.target_account_id:
                raise UserError(
                    _("Select a target account before approving rule %s.") % rule.display_name
                )
        self.write(
            {
                "state": "approved",
                "approved_by_id": self.env.user.id,
                "approved_at": fields.Datetime.now(),
            }
        )

    @api.model
    def action_seed_legacy_rules(self):
        definitions = [
            ("Interest Income", r"\bINTEREST PAYMENT\b", "credit", "Interest Income"),
            (
                "Bank Merchant Fees",
                r"\bSTOP/HOLD FEE\b|\bMONTHLY DEBIT CARD FEE\b",
                "debit",
                "Bank Merchant Fees",
            ),
            (
                "Bankcard Settlement Fee",
                r"BANKCARD-1205/MTOT DEP",
                "debit",
                "Bank Merchant Fees",
            ),
            ("Employer Payroll Taxes", r"MDES/TAXDRAFT", "debit", "Employer Payroll Taxes"),
            (
                "Tax Payments",
                r"IRS/USATAXPYMT|MSDEPTOFREVENUE/TAXPAYMENT",
                "debit",
                "Sales Tax Payable",
            ),
            (
                "Software Subscriptions",
                r"GOOGLE \*WORKSPACE|GOOGLE WORKSPACE|WWW\.SMALINK\.COM|VONAGE BUSINESS",
                "debit",
                "Software Subscriptions",
            ),
            (
                "Office Expenses",
                r"UPS\*|PAYPAL \*UPS|USPS PO|WAL WAL-MART|DOLLAR GENERAL|AMAZON\.COM",
                "debit",
                "Office Expenses",
            ),
            ("Facility Expense", r"DIXIE ELECTRIC", "debit", "Facility Expense"),
            (
                "Company Vehicle Expense",
                r"CLARK'?S #49|CIRCLE K|MARATHON|MINIT MART|MACS #|HAYDEN VALERO",
                "debit",
                "Company Vehicle Expense",
            ),
            (
                "Meals and Entertainment",
                r"SUBWAY|FIREHOUSE SUBS|JULIA'?SSTEAKHOUSE|COCA COLA",
                "debit",
                "Meals & Entertainment",
            ),
            (
                "Marketing and Advertising",
                r"SANDHILLS GLOBAL",
                "debit",
                "Marketing & Advertising",
            ),
            (
                "Parts Suppliers",
                (
                    r"SOUTHERN-GLOBAL\.COM|SHOUP MANUFACTURING|SCOTT EQUIPMENT|"
                    r"SCOTTS HYDRAULIC|MEGA PARTS|FARMLAND TRACTOR|DARRELL HARP|"
                    r"HEAVY EQUIPMENT SPECI|SPAREX AURORA|PAYPAL \*STARTFABRIK|"
                    r"FRIDAYPARTS|COLE TRACTOR|SQ \*WEST VIRGINIA MANUFAC"
                ),
                "debit",
                "Parts COGS",
            ),
            (
                "Shop and Service Equipment",
                r"PAYPAL \*DELL|UPLIFT DESK|PAYPAL \*HERMAN MILL|APPLE STORE",
                "debit",
                "Shop & Service Equipment",
            ),
        ]
        Account = self.env["account.account"]
        created = self.browse()
        for sequence, (name, pattern, direction, account_name) in enumerate(
            definitions, start=10
        ):
            if self.search_count(
                [
                    ("company_id", "=", self.env.company.id),
                    ("match_pattern", "=", pattern),
                ]
            ):
                continue
            account_domain = [("name", "=", account_name)]
            if "company_ids" in Account._fields:
                account_domain.append(("company_ids", "in", [self.env.company.id]))
            account = Account.search(account_domain, limit=1)
            created |= self.create(
                {
                    "name": name,
                    "sequence": sequence * 10,
                    "company_id": self.env.company.id,
                    "state": "draft",
                    "match_pattern": pattern,
                    "amount_direction": direction,
                    "target_account_id": account.id,
                    "target_account_name": account_name,
                    "note": _(
                        "Imported from the retired Windows/Python rule set. Review before approval."
                    ),
                }
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Bank Coding Rules"),
            "res_model": "southern.bank.coding.rule",
            "view_mode": "list,form",
            "domain": [("company_id", "=", self.env.company.id)],
        }

    def action_retire(self):
        self.write({"state": "retired", "active": False})

    def matches(self, bank_line):
        self.ensure_one()
        if self.state != "approved" or not self.active:
            return False
        if self.company_id != bank_line.company_id:
            return False
        line_date = bank_line.date
        if self.effective_from and line_date < self.effective_from:
            return False
        if self.effective_to and line_date > self.effective_to:
            return False
        amount = bank_line.amount or 0.0
        if self.amount_direction == "debit" and amount >= 0:
            return False
        if self.amount_direction == "credit" and amount <= 0:
            return False
        absolute = abs(amount)
        if self.minimum_amount and absolute < self.minimum_amount:
            return False
        if self.maximum_amount and absolute > self.maximum_amount:
            return False
        label = " ".join(
            value
            for value in (
                bank_line.payment_ref,
                getattr(bank_line, "ref", False),
                getattr(bank_line, "partner_name", False),
            )
            if value
        )
        return bool(re.search(self.match_pattern, label, re.IGNORECASE))


class SouthernBankCodingRun(models.Model):
    _name = "southern.bank.coding.run"
    _description = "Southern Bank Coding Evaluation Run"
    _inherit = ["mail.thread"]
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("Bank Coding Evaluation"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("running", "Running"),
            ("complete", "Complete"),
            ("failed", "Failed"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    started_at = fields.Datetime()
    finished_at = fields.Datetime()
    lines_scanned = fields.Integer(readonly=True)
    candidate_count = fields.Integer(readonly=True)
    unmatched_count = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    candidate_ids = fields.One2many("southern.bank.coding.candidate", "run_id")

    def action_evaluate(self):
        Rule = self.env["southern.bank.coding.rule"]
        Candidate = self.env["southern.bank.coding.candidate"]
        BankLine = self.env["account.bank.statement.line"]
        for run in self:
            run.write({"state": "running", "started_at": fields.Datetime.now()})
            try:
                lines = BankLine.search(
                    [
                        ("company_id", "=", run.company_id.id),
                        ("date", ">=", run.date_from),
                        ("date", "<=", run.date_to),
                        ("is_reconciled", "=", False),
                    ]
                )
                rules = Rule.search(
                    [
                        ("company_id", "=", run.company_id.id),
                        ("active", "=", True),
                        ("state", "=", "approved"),
                    ],
                    order="sequence, id",
                )
                created = unmatched = 0
                for bank_line in lines:
                    existing = Candidate.search(
                        [
                            ("bank_statement_line_id", "=", bank_line.id),
                            ("state", "in", ["pending", "approved", "applied"]),
                        ],
                        limit=1,
                    )
                    if existing:
                        continue
                    rule = next((item for item in rules if item.matches(bank_line)), False)
                    if not rule:
                        unmatched += 1
                        continue
                    Candidate.create(
                        {
                            "run_id": run.id,
                            "company_id": run.company_id.id,
                            "bank_statement_line_id": bank_line.id,
                            "rule_id": rule.id,
                            "target_account_id": rule.target_account_id.id,
                            "state": "pending",
                        }
                    )
                    rule.write(
                        {
                            "match_count": rule.match_count + 1,
                            "last_matched_at": fields.Datetime.now(),
                        }
                    )
                    created += 1
                run.write(
                    {
                        "state": "complete",
                        "finished_at": fields.Datetime.now(),
                        "lines_scanned": len(lines),
                        "candidate_count": created,
                        "unmatched_count": unmatched,
                    }
                )
            except Exception as error:
                run.write(
                    {
                        "state": "failed",
                        "finished_at": fields.Datetime.now(),
                        "error_message": str(error),
                    }
                )
                raise
        return True

    @api.model
    def cron_prepare_candidates(self):
        today = fields.Date.context_today(self)
        companies = self.env["res.company"].search([("name", "ilike", SOUTHERN_COMPANY_NAME)])
        for company in companies:
            run = self.create(
                {
                    "name": _("Daily Bank Coding Evaluation - %s") % today,
                    "company_id": company.id,
                    "date_from": today,
                    "date_to": today,
                }
            )
            run.action_evaluate()


class SouthernBankCodingCandidate(models.Model):
    _name = "southern.bank.coding.candidate"
    _description = "Southern Bank Coding Candidate"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    run_id = fields.Many2one(
        "southern.bank.coding.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    bank_statement_line_id = fields.Many2one(
        "account.bank.statement.line", required=True, ondelete="cascade", index=True
    )
    bank_date = fields.Date(related="bank_statement_line_id.date", store=True)
    bank_label = fields.Char(related="bank_statement_line_id.payment_ref", store=True)
    amount = fields.Monetary(related="bank_statement_line_id.amount", store=True)
    currency_id = fields.Many2one(
        "res.currency", related="bank_statement_line_id.currency_id", store=True
    )
    rule_id = fields.Many2one("southern.bank.coding.rule", required=True, ondelete="restrict")
    target_account_id = fields.Many2one("account.account", required=True, ondelete="restrict")
    state = fields.Selection(
        [
            ("pending", "Pending Review"),
            ("approved", "Approved"),
            ("applied", "Applied"),
            ("rejected", "Rejected"),
            ("exception", "Exception"),
        ],
        default="pending",
        required=True,
        tracking=True,
        index=True,
    )
    approved_by_id = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)
    applied_by_id = fields.Many2one("res.users", readonly=True)
    applied_at = fields.Datetime(readonly=True)
    review_note = fields.Text(tracking=True)
    error_message = fields.Text(readonly=True)

    def action_approve(self):
        unsafe = self.filtered(
            lambda candidate: is_unsafe_merchant_target(
                candidate.bank_statement_line_id,
                candidate.target_account_id,
            )
        )
        if unsafe:
            raise UserError(
                _(
                    "Merchant settlements cannot be approved directly to revenue, "
                    "receivables, tax, or suspense. Match the card/payment batch to "
                    "Outstanding Receipts and record merchant fees separately."
                )
            )
        self.write(
            {
                "state": "approved",
                "approved_by_id": self.env.user.id,
                "approved_at": fields.Datetime.now(),
            }
        )

    def action_reject(self):
        self.write({"state": "rejected"})

    def action_apply(self):
        for candidate in self:
            if candidate.state != "approved":
                raise UserError(_("Approve this candidate before applying it."))
            bank_line = candidate.bank_statement_line_id
            if is_unsafe_merchant_target(bank_line, candidate.target_account_id):
                candidate.write(
                    {
                        "state": "exception",
                        "error_message": _(
                            "Unsafe merchant-settlement target. Use Outstanding Receipts "
                            "and a separately supported merchant-fee line."
                        ),
                    }
                )
                continue
            if bank_line.is_reconciled:
                raise UserError(_("The bank line is already reconciled."))
            suspense_lines = bank_line.move_id.line_ids.filtered(
                lambda line: line.account_id.name == "Bank Suspense Account"
            )
            if len(suspense_lines) != 1:
                candidate.write(
                    {
                        "state": "exception",
                        "error_message": _(
                            "Expected exactly one Bank Suspense Account line; found %s."
                        )
                        % len(suspense_lines),
                    }
                )
                continue
            account_companies = getattr(candidate.target_account_id, "company_ids", False)
            if account_companies and bank_line.company_id not in account_companies:
                candidate.write(
                    {
                        "state": "exception",
                        "error_message": _(
                            "The target account is not available to the bank line company."
                        ),
                    }
                )
                continue
            try:
                with self.env.cr.savepoint():
                    suspense_lines.write({"account_id": candidate.target_account_id.id})
                    bank_line.write(
                        {
                            "southern_review_status": "reviewed",
                            "southern_review_note": _(
                                "Applied approved bank coding candidate %s using rule %s."
                            )
                            % (candidate.id, candidate.rule_id.display_name),
                        }
                    )
            except Exception as error:
                candidate.write(
                    {
                        "state": "exception",
                        "error_message": str(error),
                    }
                )
                continue
            candidate.write(
                {
                    "state": "applied",
                    "applied_by_id": self.env.user.id,
                    "applied_at": fields.Datetime.now(),
                    "error_message": False,
                }
            )
        return True
