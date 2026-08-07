from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SouthernAccountingAutomationPolicy(models.Model):
    _name = "southern.accounting.automation.policy"
    _description = "Southern Accounting Automation Policy"
    _inherit = ["mail.thread"]  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
    _order = "company_id, lane, policy_version desc, id desc"

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    lane = fields.Selection(
        [("bank_coding", "Bank Coding")],
        default="bank_coding",
        required=True,
        tracking=True,
        index=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("active", "Active"),
            ("superseded", "Superseded"),
            ("retired", "Retired"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    mode = fields.Selection(
        [
            ("observe", "Observe"),
            ("candidate", "Candidate"),
            ("guarded_apply", "Guarded Apply"),
        ],
        default="observe",
        required=True,
        tracking=True,
        help="Controls the highest action the automation may take.",
    )
    policy_version = fields.Integer(default=1, required=True, readonly=True, copy=False)
    effective_from = fields.Datetime(default=fields.Datetime.now, required=True, tracking=True)
    superseded_by_id = fields.Many2one(
        "southern.accounting.automation.policy", readonly=True, copy=False
    )
    emergency_stop = fields.Boolean(
        tracking=True,
        help="When enabled, guarded apply is blocked but observe and candidate modes may continue.",
    )
    per_line_limit = fields.Monetary(default=2500.0, required=True, tracking=True)
    daily_apply_limit = fields.Monetary(default=10000.0, required=True, tracking=True)
    currency_id = fields.Many2one(
        "res.currency", related="company_id.currency_id", store=True, readonly=True
    )
    protected_account_ids = fields.Many2many(
        "account.account",
        "southern_accounting_policy_protected_account_rel",
        "policy_id",
        "account_id",
        string="Protected Accounts",
        help="Target accounts that are never eligible for autonomous application.",
    )
    note = fields.Text()

    @api.constrains("state", "company_id", "lane")
    def _check_one_active_policy(self):
        for policy in self.filtered(lambda item: item.state == "active"):
            duplicate = self.search_count(
                [
                    ("id", "!=", policy.id),
                    ("company_id", "=", policy.company_id.id),
                    ("lane", "=", policy.lane),
                    ("state", "=", "active"),
                ]
            )
            if duplicate:
                raise ValidationError(
                    _("Only one active accounting automation policy is allowed per company and lane.")
                )

    def write(self, values):
        locked_fields = {
            "lane",
            "mode",
            "per_line_limit",
            "daily_apply_limit",
            "protected_account_ids",
        }
        if locked_fields.intersection(values):
            locked = self.filtered(
                lambda policy: policy.state in {"active", "superseded", "retired"}
                or self.env["southern.bank.coding.candidate"].search_count(
                    [
                        ("policy_id", "=", policy.id),
                        ("policy_eligible", "=", True),
                    ]
                )
            )
            if locked:
                raise ValidationError(
                    _(
                        "Versioned accounting policies cannot be edited after activation or candidate authorization. "
                        "Create a new version instead."
                    )
                )
        return super().write(values)

    def action_activate(self):
        for policy in self:
            existing = self.search(
                [
                    ("id", "!=", policy.id),
                    ("company_id", "=", policy.company_id.id),
                    ("lane", "=", policy.lane),
                    ("state", "=", "active"),
                ]
            )
            if existing:
                existing.write({"state": "superseded", "superseded_by_id": policy.id})
            policy.write({"state": "active", "effective_from": fields.Datetime.now()})

    def action_retire(self):
        self.write({"state": "retired"})

    def action_enable_emergency_stop(self):
        self.write({"emergency_stop": True})

    def action_clear_emergency_stop(self):
        self.write({"emergency_stop": False})

    @api.model
    def action_create_default_bank_coding_policy(self):
        policy = self.current_policy(self.env.company, "bank_coding")
        if not policy:
            policy = self.create(
                {
                    "name": _("Southern Bank Coding Automation Policy"),
                    "company_id": self.env.company.id,
                    "lane": "bank_coding",
                    "mode": "observe",
                    "state": "draft",
                    "per_line_limit": 2500.0,
                    "daily_apply_limit": 10000.0,
                    "note": _(
                        "Default V1 policy. Activate only after observe and candidate cycles are reviewed."
                    ),
                }
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Accounting Automation Policy"),
            "res_model": "southern.accounting.automation.policy",
            "view_mode": "form",
            "res_id": policy.id,
        }

    def action_new_version(self):
        self.ensure_one()
        new_policy = self.copy(
            {
                "name": _("%s v%s") % (self.name, self.policy_version + 1),
                "state": "draft",
                "policy_version": self.policy_version + 1,
                "effective_from": fields.Datetime.now(),
                "superseded_by_id": False,
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Accounting Automation Policy"),
            "res_model": "southern.accounting.automation.policy",
            "view_mode": "form",
            "res_id": new_policy.id,
        }

    @api.model
    def current_policy(self, company, lane="bank_coding"):
        return self.search(
            [
                ("company_id", "=", company.id),
                ("lane", "=", lane),
                ("state", "=", "active"),
            ],
            order="policy_version desc, id desc",
            limit=1,
        )


class SouthernAccountingAutomationRun(models.Model):
    _name = "southern.accounting.automation.run"
    _description = "Southern Accounting Automation Run"
    _inherit = ["mail.thread"]  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("Accounting Automation Run"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    lane = fields.Selection(
        [("bank_coding", "Bank Coding")],
        default="bank_coding",
        required=True,
        index=True,
    )
    mode = fields.Selection(
        [
            ("observe", "Observe"),
            ("candidate", "Candidate"),
            ("guarded_apply", "Guarded Apply"),
        ],
        default="observe",
        required=True,
        index=True,
    )
    worker = fields.Selection(
        [("odoo", "Odoo"), ("aws", "AWS"), ("codex", "Codex"), ("manual", "Manual")],
        default="odoo",
        required=True,
        tracking=True,
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("running", "Running"),
            ("succeeded", "Succeeded"),
            ("blocked", "Blocked"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="queued",
        required=True,
        tracking=True,
        index=True,
    )
    policy_id = fields.Many2one("southern.accounting.automation.policy", ondelete="restrict")
    policy_version = fields.Integer(readonly=True)
    date_from = fields.Date(required=True, default=fields.Date.context_today)
    date_to = fields.Date(required=True, default=fields.Date.context_today)
    started_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    finished_at = fields.Datetime()
    external_run_id = fields.Char(index=True, copy=False)
    command_id = fields.Char(index=True, copy=False)
    artifact_uri = fields.Char()
    artifact_sha256 = fields.Char()
    artifact_schema_version = fields.Char(default="1.0")
    lines_scanned = fields.Integer(readonly=True)
    finding_count = fields.Integer(readonly=True)
    candidate_count = fields.Integer(readonly=True)
    auto_applied_count = fields.Integer(readonly=True)
    blocked_count = fields.Integer(readonly=True)
    evidence_summary = fields.Text()
    error_message = fields.Text(readonly=True)
    finding_ids = fields.One2many("southern.accounting.automation.finding", "run_id")
    bank_coding_run_id = fields.Many2one("southern.bank.coding.run", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            policy = False
            if values.get("policy_id"):
                policy = self.env["southern.accounting.automation.policy"].browse(values["policy_id"])
            elif values.get("company_id"):
                policy = self.env["southern.accounting.automation.policy"].current_policy(
                    self.env["res.company"].browse(values["company_id"]), values.get("lane") or "bank_coding"
                )
            if policy:
                values.setdefault("policy_id", policy.id)
                values.setdefault("policy_version", policy.policy_version)
                values.setdefault("mode", policy.mode)
        return super().create(vals_list)

    def action_cancel(self):
        self.write({"state": "cancelled", "finished_at": fields.Datetime.now()})


class SouthernAccountingAutomationFinding(models.Model):
    _name = "southern.accounting.automation.finding"
    _description = "Southern Accounting Automation Finding"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
    _order = "severity, bank_date desc, id desc"

    run_id = fields.Many2one(
        "southern.accounting.automation.run", required=True, ondelete="cascade", index=True
    )
    company_id = fields.Many2one("res.company", related="run_id.company_id", store=True, index=True)
    lane = fields.Selection(related="run_id.lane", store=True)
    mode = fields.Selection(related="run_id.mode", store=True)
    bank_statement_line_id = fields.Many2one("account.bank.statement.line", index=True)
    candidate_id = fields.Many2one("southern.bank.coding.candidate", index=True)
    bank_date = fields.Date(related="bank_statement_line_id.date", store=True)
    amount = fields.Monetary(related="bank_statement_line_id.amount", store=True)
    currency_id = fields.Many2one(
        "res.currency", related="bank_statement_line_id.currency_id", store=True
    )
    severity = fields.Selection(
        [("high", "High"), ("medium", "Medium"), ("low", "Low")],
        default="medium",
        required=True,
        index=True,
    )
    reason_code = fields.Selection(
        [
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
        ],
        required=True,
        index=True,
    )
    reason_note = fields.Text()
    ai_explanation = fields.Text()
    state = fields.Selection(
        [
            ("open", "Open"),
            ("reviewed", "Reviewed"),
            ("resolved", "Resolved"),
            ("ignored", "Ignored"),
        ],
        default="open",
        required=True,
        tracking=True,
        index=True,
    )

    def action_mark_reviewed(self):
        self.write({"state": "reviewed"})

    def action_resolve(self):
        self.write({"state": "resolved"})
