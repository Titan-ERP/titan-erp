import hashlib
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

REASON_CODES = [
    ("OVER_LINE_LIMIT", "Over Line Limit"),
    ("DAILY_LIMIT_REACHED", "Daily Limit Reached"),
    ("MERCHANT_SETTLEMENT", "Merchant Settlement"),
    ("GENERIC_CHECK", "Generic Check"),
    ("TRANSFER_RISK", "Transfer Risk"),
    ("LOAN_PAYMENT", "Loan Payment"),
    ("TAX_PAYMENT", "Tax Payment"),
    ("MULTIPLE_SUSPENSE_LINES", "Multiple Suspense Lines"),
    ("ALREADY_RECONCILED", "Already Reconciled"),
    ("SNAPSHOT_CHANGED", "Snapshot Changed"),
    ("PROTECTED_ACCOUNT", "Protected Account"),
    ("NO_DETERMINISTIC_RULE", "No Deterministic Rule"),
    ("OBSERVED_DETERMINISTIC_MATCH", "Observed Deterministic Match"),
    ("COMPANY_MISMATCH", "Company Mismatch"),
]


PROTECTED_PATTERNS = [
    ("MERCHANT_SETTLEMENT", r"MERCHANT SERVICE|MERCHANT DEPOSIT|BANKCARD|MTOT DEP|NET SETTLE"),
    ("GENERIC_CHECK", r"\bCHECK\b|TELLER CHECK|INTUIT.*/CHECKS"),
    ("TRANSFER_RISK", r"TRANSFER FROM|TRANSFER TO|TELEPHONE TRF|ATS - CHECKING|AUTOPAY PAYMENT|PAYMENT THANK YOU"),
    ("LOAN_PAYMENT", r"\bLOAN\b| LN |TO LN|PAYDOWN|PAY DOWN"),
    ("TAX_PAYMENT", r"IRS|MSDEPTOFREVENUE|TAXPAYMENT|TAXDRAFT"),
]


def protected_reason_code(bank_line):
    ref = " ".join(
        value
        for value in (
            bank_line.payment_ref,
            getattr(bank_line, "ref", False),
            getattr(bank_line, "partner_name", False),
        )
        if value
    )
    for code, pattern in PROTECTED_PATTERNS:
        if re.search(pattern, ref or "", re.IGNORECASE):
            if code == "GENERIC_CHECK" and bank_line.partner_id:
                continue
            return code
    return False


def hash_part(value):
    if hasattr(value, "_name") and hasattr(value, "id"):
        return str(value.id or "")
    return str(value or "")


def build_evaluation_hash(company_id, bank_line, move_line, target_account, rule, policy):
    values = [
        company_id,
        bank_line.id,
        bank_line.amount,
        bank_line.date,
        bank_line.payment_ref,
        move_line.id,
        move_line.account_id.id if move_line.account_id else "",
        target_account.id if target_account else "",
        bank_line.partner_id.id if bank_line.partner_id else "",
        bank_line.is_reconciled,
        rule.id if rule else "",
        rule.rule_version if rule else "",
        policy.id if policy else "",
        policy.policy_version if policy else "",
    ]
    payload = "|".join(hash_part(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_application_key(candidate):
    values = [
        candidate.id,
        candidate.evaluation_hash,
        candidate.target_account_id.id if candidate.target_account_id else "",
        candidate.policy_id.id if candidate.policy_id else "",
        candidate.policy_version,
    ]
    payload = "|".join(hash_part(value) for value in values)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SouthernBankCodingRule(models.Model):
    _name = "southern.bank.coding.rule"
    _description = "Southern Bank Coding Rule"
    _inherit = ["mail.thread"]  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
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
    rule_version = fields.Integer(default=1, required=True, readonly=True, copy=False)
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

    def write(self, values):
        versioned_fields = {
            "match_pattern",
            "amount_direction",
            "minimum_amount",
            "maximum_amount",
            "target_account_id",
            "effective_from",
            "effective_to",
        }
        if versioned_fields.intersection(values):
            locked = self.filtered(lambda rule: rule.state == "approved")
            if locked:
                raise ValidationError(
                    _("Approved deterministic bank coding rules are version-locked. Retire and copy the rule for changes.")
                )
        return super().write(values)

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
    _inherit = ["mail.thread"]  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
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
    mode = fields.Selection(
        [
            ("observe", "Observe"),
            ("candidate", "Candidate"),
            ("guarded_apply", "Guarded Apply"),
        ],
        default="observe",
        required=True,
        tracking=True,
        index=True,
    )
    worker = fields.Selection(
        [("odoo", "Odoo"), ("aws", "AWS"), ("codex", "Codex"), ("manual", "Manual")],
        default="odoo",
        required=True,
        tracking=True,
    )
    automation_run_id = fields.Many2one(
        "southern.accounting.automation.run", readonly=True, copy=False
    )
    policy_id = fields.Many2one("southern.accounting.automation.policy", ondelete="restrict")
    policy_version = fields.Integer(readonly=True)
    lines_scanned = fields.Integer(readonly=True)
    candidate_count = fields.Integer(readonly=True)
    auto_applied_count = fields.Integer(readonly=True)
    finding_count = fields.Integer(readonly=True)
    unmatched_count = fields.Integer(readonly=True)
    error_message = fields.Text(readonly=True)
    candidate_ids = fields.One2many("southern.bank.coding.candidate", "run_id")

    def _active_policy(self):
        self.ensure_one()
        if self.policy_id:
            return self.policy_id
        return self.env["southern.accounting.automation.policy"].current_policy(
            self.company_id, "bank_coding"
        )

    def _create_finding(self, automation_run, bank_line, reason_code, note, severity="medium", candidate=False):
        return self.env["southern.accounting.automation.finding"].create(
            {
                "run_id": automation_run.id,
                "bank_statement_line_id": bank_line.id,
                "candidate_id": candidate.id if candidate else False,
                "severity": severity,
                "reason_code": reason_code,
                "reason_note": note,
            }
        )

    def action_evaluate(self):
        Rule = self.env["southern.bank.coding.rule"]
        Candidate = self.env["southern.bank.coding.candidate"]
        BankLine = self.env["account.bank.statement.line"]
        for run in self:
            run.write({"state": "running", "started_at": fields.Datetime.now()})
            policy = run._active_policy()
            mode = policy.mode if policy else run.mode
            automation_run = self.env["southern.accounting.automation.run"].create(
                {
                    "name": _("Bank Coding Automation - %s to %s") % (run.date_from, run.date_to),
                    "company_id": run.company_id.id,
                    "lane": "bank_coding",
                    "mode": mode,
                    "worker": run.worker,
                    "state": "running",
                    "policy_id": policy.id if policy else False,
                    "policy_version": policy.policy_version if policy else 0,
                    "date_from": run.date_from,
                    "date_to": run.date_to,
                    "bank_coding_run_id": run.id,
                }
            )
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
                created = unmatched = findings = auto_applied = 0
                for bank_line in lines:
                    if bank_line.is_reconciled:
                        self._create_finding(
                            automation_run,
                            bank_line,
                            "ALREADY_RECONCILED",
                            _("Bank line was reconciled before evaluation completed."),
                            "low",
                        )
                        findings += 1
                        continue
                    protected_code = protected_reason_code(bank_line)
                    if protected_code:
                        self._create_finding(
                            automation_run,
                            bank_line,
                            protected_code,
                            _("Protected bank-line pattern is review-only in V1."),
                            "high" if protected_code in {"TRANSFER_RISK", "LOAN_PAYMENT"} else "medium",
                        )
                        findings += 1
                        unmatched += 1
                        continue
                    existing = Candidate.search(
                        [
                            ("bank_statement_line_id", "=", bank_line.id),
                            (
                                "state",
                                "in",
                                [
                                    "candidate",
                                    "eligible",
                                    "review_required",
                                    "blocked",
                                    "pending",
                                    "approved",
                                    "applied",
                                ],
                            ),
                        ],
                        limit=1,
                    )
                    if existing:
                        continue
                    rule = next((item for item in rules if item.matches(bank_line)), False)
                    if not rule:
                        self._create_finding(
                            automation_run,
                            bank_line,
                            "NO_DETERMINISTIC_RULE",
                            _("No approved deterministic Odoo bank coding rule matched this line."),
                            "medium",
                        )
                        findings += 1
                        unmatched += 1
                        continue
                    if mode == "observe":
                        self._create_finding(
                            automation_run,
                            bank_line,
                            "OBSERVED_DETERMINISTIC_MATCH",
                            _("Observe mode found a deterministic rule but did not create a candidate."),
                            "low",
                        )
                        findings += 1
                        continue
                    candidate = Candidate.create(
                        {
                            "run_id": run.id,
                            "automation_run_id": automation_run.id,
                            "company_id": run.company_id.id,
                            "bank_statement_line_id": bank_line.id,
                            "rule_id": rule.id,
                            "target_account_id": rule.target_account_id.id,
                            "policy_id": policy.id if policy else False,
                            "policy_version": policy.policy_version if policy else 0,
                            "mode": mode,
                            "match_type": "deterministic",
                            "deterministic_match": True,
                            "ai_confidence": 0.0,
                            "state": "candidate",
                        }
                    )
                    candidate.action_evaluate_policy()
                    rule.write(
                        {
                            "match_count": rule.match_count + 1,
                            "last_matched_at": fields.Datetime.now(),
                        }
                    )
                    created += 1
                    if mode == "guarded_apply" and candidate.auto_apply_eligible:
                        candidate.guarded_apply_candidate()
                        if candidate.state == "applied":
                            auto_applied += 1
                run.write(
                    {
                        "state": "complete",
                        "finished_at": fields.Datetime.now(),
                        "mode": mode,
                        "automation_run_id": automation_run.id,
                        "policy_id": policy.id if policy else False,
                        "policy_version": policy.policy_version if policy else 0,
                        "lines_scanned": len(lines),
                        "candidate_count": created,
                        "auto_applied_count": auto_applied,
                        "finding_count": findings,
                        "unmatched_count": unmatched,
                    }
                )
                automation_run.write(
                    {
                        "state": "succeeded",
                        "finished_at": fields.Datetime.now(),
                        "lines_scanned": len(lines),
                        "candidate_count": created,
                        "auto_applied_count": auto_applied,
                        "finding_count": findings,
                        "blocked_count": unmatched,
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
                automation_run.write(
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
        for company in self.env["res.company"].search([]):
            policy = self.env["southern.accounting.automation.policy"].current_policy(
                company, "bank_coding"
            )
            run = self.create(
                {
                    "name": _("Daily Bank Coding Evaluation - %s") % today,
                    "company_id": company.id,
                    "date_from": today,
                    "date_to": today,
                    "mode": policy.mode if policy else "observe",
                    "worker": "odoo",
                    "policy_id": policy.id if policy else False,
                    "policy_version": policy.policy_version if policy else 0,
                }
            )
            run.action_evaluate()


class SouthernBankCodingCandidate(models.Model):
    _name = "southern.bank.coding.candidate"
    _description = "Southern Bank Coding Candidate"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
    _order = "create_date desc, id desc"

    run_id = fields.Many2one(
        "southern.bank.coding.run", required=True, ondelete="cascade", index=True
    )
    automation_run_id = fields.Many2one(
        "southern.accounting.automation.run", readonly=True, copy=False, index=True
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
    match_type = fields.Selection(
        [
            ("deterministic", "Deterministic"),
            ("ai_suggested", "AI Suggested"),
            ("manual", "Manual"),
        ],
        default="deterministic",
        required=True,
        tracking=True,
        index=True,
    )
    deterministic_match = fields.Boolean(default=False, tracking=True)
    ai_confidence = fields.Float(
        digits=(3, 2),
        help="AI confidence is advisory only and never authorizes accounting changes.",
    )
    ai_suggested_partner_id = fields.Many2one("res.partner", string="AI Suggested Partner")
    ai_suggested_account_id = fields.Many2one("account.account", string="AI Suggested Account")
    ai_explanation = fields.Text()
    policy_id = fields.Many2one("southern.accounting.automation.policy", ondelete="restrict")
    policy_version = fields.Integer(readonly=True)
    policy_eligible = fields.Boolean(readonly=True, index=True)
    auto_apply_eligible = fields.Boolean(readonly=True, index=True)
    reason_code = fields.Selection(REASON_CODES, readonly=True, index=True)
    reason_note = fields.Text(readonly=True)
    mode = fields.Selection(
        [
            ("observe", "Observe"),
            ("candidate", "Candidate"),
            ("guarded_apply", "Guarded Apply"),
        ],
        default="candidate",
        required=True,
        tracking=True,
        index=True,
    )
    suspense_move_line_id = fields.Many2one("account.move.line", readonly=True)
    evaluation_hash = fields.Char(readonly=True, copy=False, index=True)
    applied_from_account_id = fields.Many2one("account.account", readonly=True, copy=False)
    applied_to_account_id = fields.Many2one("account.account", readonly=True, copy=False)
    applied_move_line_id = fields.Many2one("account.move.line", readonly=True, copy=False)
    application_key = fields.Char(readonly=True, copy=False, index=True)
    application_attempted_at = fields.Datetime(readonly=True, copy=False)
    application_result = fields.Selection(
        [
            ("not_attempted", "Not Attempted"),
            ("applied", "Applied"),
            ("already_applied", "Already Applied"),
            ("blocked", "Blocked"),
            ("stale", "Stale"),
            ("failed", "Failed"),
        ],
        default="not_attempted",
        readonly=True,
        copy=False,
        index=True,
    )
    application_error = fields.Text(readonly=True, copy=False)
    state = fields.Selection(
        [
            ("observed", "Observed"),
            ("evaluated", "Evaluated"),
            ("candidate", "Candidate"),
            ("eligible", "Eligible"),
            ("review_required", "Review Required"),
            ("blocked", "Blocked"),
            ("pending", "Pending Review"),
            ("approved", "Approved"),
            ("applied", "Applied"),
            ("rejected", "Rejected"),
            ("stale", "Stale"),
            ("exception", "Exception"),
        ],
        default="candidate",
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

    _sql_constraints = [  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
        (
            "southern_bank_coding_candidate_application_key_unique",
            "unique(application_key)",
            "This bank coding candidate application has already been processed.",
        )
    ]

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        for candidate in records:
            if not candidate.policy_id:
                policy = self.env["southern.accounting.automation.policy"].current_policy(
                    candidate.company_id, "bank_coding"
                )
                if policy:
                    candidate.write(
                        {
                            "policy_id": policy.id,
                            "policy_version": policy.policy_version,
                            "mode": policy.mode,
                        }
                    )
            if not candidate.evaluation_hash:
                candidate._refresh_evaluation_snapshot()
        return records

    def _suspense_lines(self):
        self.ensure_one()
        return self.bank_statement_line_id.move_id.line_ids.filtered(
            lambda line: line.account_id.name == "Bank Suspense Account"
        )

    def _refresh_evaluation_snapshot(self):
        for candidate in self:
            suspense_lines = candidate._suspense_lines()
            values = {"suspense_move_line_id": False, "evaluation_hash": False}
            if len(suspense_lines) == 1:
                line = suspense_lines[0]
                values.update(
                    {
                        "suspense_move_line_id": line.id,
                        "evaluation_hash": build_evaluation_hash(
                            candidate.company_id.id,
                            candidate.bank_statement_line_id,
                            line,
                            candidate.target_account_id,
                            candidate.rule_id,
                            candidate.policy_id,
                        ),
                        "application_key": False,
                    }
                )
            super(SouthernBankCodingCandidate, candidate).write(values)

    def _current_evaluation_hash(self):
        self.ensure_one()
        suspense_lines = self._suspense_lines()
        if len(suspense_lines) != 1:
            return False
        return build_evaluation_hash(
            self.company_id.id,
            self.bank_statement_line_id,
            suspense_lines[0],
            self.target_account_id,
            self.rule_id,
            self.policy_id,
        )

    def _set_policy_block(self, code, note):
        state = "review_required" if code in {"MERCHANT_SETTLEMENT", "GENERIC_CHECK", "NO_DETERMINISTIC_RULE"} else "blocked"
        self.write(
            {
                "policy_eligible": False,
                "auto_apply_eligible": False,
                "reason_code": code,
                "reason_note": note,
                "state": state,
            }
        )

    def _daily_applied_total(self, policy):
        today = fields.Date.context_today(self)
        applied_today = self.search(
            [
                ("policy_id", "=", policy.id),
                ("state", "=", "applied"),
                ("application_result", "=", "applied"),
                ("applied_at", ">=", fields.Datetime.to_string(today)),
            ]
        )
        return sum(abs(item.amount or 0.0) for item in applied_today)

    def _assert_company_isolation(self, suspense_line):
        self.ensure_one()
        company = self.company_id
        bank_line = self.bank_statement_line_id
        move = bank_line.move_id
        related = [
            ("bank line", bank_line.company_id),
            ("bank move", move.company_id),
            ("suspense line", suspense_line.company_id),
            ("rule", self.rule_id.company_id),
            ("policy", self.policy_id.company_id),
            ("run", self.run_id.company_id),
        ]
        if self.automation_run_id:
            related.append(("automation run", self.automation_run_id.company_id))
        if bank_line.journal_id and bank_line.journal_id.company_id != company:
            raise UserError(_("Company mismatch: bank journal is not in the candidate company."))
        for label, related_company in related:
            if related_company and related_company != company:
                raise UserError(_("Company mismatch: %s is not in the candidate company.") % label)
        account_companies = getattr(self.target_account_id, "company_ids", False)
        if account_companies and company not in account_companies:
            raise UserError(_("Company mismatch: target account is not available to the candidate company."))
        account_company = getattr(self.target_account_id, "company_id", False)
        if account_company and account_company != company:
            raise UserError(_("Company mismatch: target account is not in the candidate company."))

    def _mark_application_blocked(self, code, note, result="blocked"):
        self.write(
            {
                "state": "stale" if result == "stale" else "blocked",
                "policy_eligible": False,
                "auto_apply_eligible": False,
                "reason_code": code,
                "reason_note": note,
                "application_attempted_at": fields.Datetime.now(),
                "application_result": result,
                "application_error": note,
                "error_message": note,
            }
        )

    def _prepare_guarded_apply(self):
        self.ensure_one()
        if self.state == "applied":
            self.write(
                {
                    "application_attempted_at": fields.Datetime.now(),
                    "application_error": _("Already applied; no second write performed."),
                }
            )
            return False
        self.action_evaluate_policy()
        if not self.policy_eligible or not self.auto_apply_eligible:
            self._mark_application_blocked(
                self.reason_code or "NO_DETERMINISTIC_RULE",
                self.reason_note or _("Candidate is not eligible for guarded apply."),
            )
            return False
        suspense_lines = self._suspense_lines()
        if len(suspense_lines) != 1:
            self._mark_application_blocked(
                "MULTIPLE_SUSPENSE_LINES",
                _("Expected exactly one Bank Suspense Account line; found %s.") % len(suspense_lines),
            )
            return False
        suspense_line = suspense_lines[0]
        try:
            self._assert_company_isolation(suspense_line)
        except UserError as error:
            self._mark_application_blocked("COMPANY_MISMATCH", str(error))
            return False
        current_hash = self._current_evaluation_hash()
        if not current_hash or current_hash != self.evaluation_hash:
            self._mark_application_blocked(
                "SNAPSHOT_CHANGED",
                _("The bank line or suspense move-line snapshot changed."),
                "stale",
            )
            return False
        application_key = build_application_key(self)
        already = self.search(
            [
                ("id", "!=", self.id),
                ("application_key", "=", application_key),
                ("state", "=", "applied"),
            ],
            limit=1,
        )
        if already or self.application_result == "applied":
            self.write(
                {
                    "application_key": application_key,
                    "application_attempted_at": fields.Datetime.now(),
                    "application_result": "already_applied",
                    "application_error": False,
                }
            )
            return False
        self.write({"application_key": application_key})
        return suspense_line

    def action_evaluate_policy(self):
        for candidate in self:
            policy = candidate.policy_id
            if not policy or policy.state != "active":
                candidate._set_policy_block(
                    "NO_DETERMINISTIC_RULE",
                    _("No active Odoo accounting automation policy is available."),
                )
                continue
            if policy.policy_version != candidate.policy_version:
                candidate._set_policy_block(
                    "SNAPSHOT_CHANGED",
                    _("The active policy version differs from the candidate evaluation version."),
                )
                continue
            if candidate.match_type != "deterministic" or not candidate.deterministic_match:
                candidate._set_policy_block(
                    "NO_DETERMINISTIC_RULE",
                    _("AI/manual suggestions are review-only and cannot authorize autonomous writes."),
                )
                continue
            if policy.emergency_stop:
                candidate._set_policy_block(
                    "PROTECTED_ACCOUNT",
                    _("Emergency stop is enabled for this accounting automation policy."),
                )
                continue
            if candidate.bank_statement_line_id.is_reconciled:
                candidate._set_policy_block("ALREADY_RECONCILED", _("The bank line is already reconciled."))
                continue
            protected_code = protected_reason_code(candidate.bank_statement_line_id)
            if protected_code:
                candidate._set_policy_block(
                    protected_code, _("Protected bank-line pattern is review-only in V1.")
                )
                continue
            suspense_lines = candidate._suspense_lines()
            if len(suspense_lines) != 1:
                candidate._set_policy_block(
                    "MULTIPLE_SUSPENSE_LINES",
                    _("Expected exactly one Bank Suspense Account line; found %s.") % len(suspense_lines),
                )
                continue
            candidate._refresh_evaluation_snapshot()
            if abs(candidate.amount or 0.0) > policy.per_line_limit:
                candidate._set_policy_block(
                    "OVER_LINE_LIMIT",
                    _("Line amount exceeds the autonomous policy limit."),
                )
                continue
            if candidate.target_account_id in policy.protected_account_ids:
                candidate._set_policy_block(
                    "PROTECTED_ACCOUNT",
                    _("The target account is protected from autonomous application."),
                )
                continue
            applied_total = candidate._daily_applied_total(policy)
            if applied_total + abs(candidate.amount or 0.0) > policy.daily_apply_limit:
                candidate._set_policy_block(
                    "DAILY_LIMIT_REACHED",
                    _("Daily autonomous apply limit would be exceeded."),
                )
                continue
            candidate.write(
                {
                    "policy_eligible": True,
                    "auto_apply_eligible": candidate.mode == "guarded_apply",
                    "reason_code": False,
                    "reason_note": _("Eligible under deterministic Odoo policy."),
                    "state": "eligible" if candidate.mode == "guarded_apply" else "candidate",
                }
            )
        return True

    def action_approve(self):
        self.write(
            {
                "state": "eligible",
                "approved_by_id": self.env.user.id,
                "approved_at": fields.Datetime.now(),
            }
        )

    def action_reject(self):
        self.write({"state": "rejected"})

    def _apply_authorized_suspense_change(self, suspense_line, *, autonomous):
        self.ensure_one()
        before_account = suspense_line.account_id
        suspense_line.sudo().write({"account_id": self.target_account_id.id})
        self.bank_statement_line_id.sudo().write(
            {
                "southern_review_status": "reviewed",
                "southern_review_note": _(
                    "%s bank coding candidate %s using rule %s."
                )
                % (
                    _("Autonomously applied") if autonomous else _("Applied approved"),
                    self.id,
                    self.rule_id.display_name,
                ),
            }
        )
        self.write(
            {
                "state": "applied",
                "applied_by_id": self.env.user.id,
                "applied_at": fields.Datetime.now(),
                "applied_from_account_id": before_account.id,
                "applied_to_account_id": self.target_account_id.id,
                "applied_move_line_id": suspense_line.id,
                "application_attempted_at": fields.Datetime.now(),
                "application_result": "applied",
                "application_error": False,
                "error_message": False,
            }
        )

    def guarded_apply_candidate(self):
        for candidate in self:
            suspense_line = candidate._prepare_guarded_apply()
            if not suspense_line:
                continue
            try:
                with self.env.cr.savepoint():
                    candidate._apply_authorized_suspense_change(suspense_line, autonomous=True)
            except Exception as error:  # noqa: BLE001 - Guarded automation must fail closed per candidate.
                candidate.write(
                    {
                        "state": "exception",
                        "application_attempted_at": fields.Datetime.now(),
                        "application_result": "failed",
                        "application_error": str(error),
                        "error_message": str(error),
                    }
                )
        return True

    def action_apply(self):
        for candidate in self:
            if candidate.state not in {"approved", "eligible"}:
                raise UserError(_("Approve this candidate before applying it."))
            candidate.action_evaluate_policy()
            if not candidate.policy_eligible:
                raise UserError(
                    _("Candidate is not eligible under Odoo policy: %s")
                    % (candidate.reason_note or candidate.reason_code or _("unknown reason"))
                )
            suspense_lines = candidate._suspense_lines()
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
            current_hash = candidate._current_evaluation_hash()
            if not current_hash or current_hash != candidate.evaluation_hash:
                candidate._mark_application_blocked(
                    "SNAPSHOT_CHANGED",
                    _("The bank line or suspense move-line snapshot changed."),
                    "stale",
                )
                continue
            bank_line = candidate.bank_statement_line_id
            if bank_line.is_reconciled:
                raise UserError(_("The bank line is already reconciled."))
            candidate._assert_company_isolation(suspense_lines[0])
            try:
                with self.env.cr.savepoint():
                    candidate.write({"application_key": build_application_key(candidate)})
                    candidate._apply_authorized_suspense_change(suspense_lines[0], autonomous=False)
            except Exception as error:  # noqa: BLE001 - Odoo should capture per-candidate apply failures and continue.
                candidate.write(
                    {
                        "state": "exception",
                        "application_attempted_at": fields.Datetime.now(),
                        "application_result": "failed",
                        "application_error": str(error),
                        "error_message": str(error),
                    }
                )
                continue
        return True
