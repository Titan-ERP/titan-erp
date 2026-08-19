from datetime import datetime, time, timedelta

from odoo import _, api, fields, models


class SouthernOperationsDailyControl(models.Model):
    _name = "southern.operations.daily.control"
    _description = "Southern Daily Operations Control"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "control_date desc, company_id"

    name = fields.Char(compute="_compute_name", store=True)
    control_date = fields.Date(
        required=True, default=fields.Date.context_today, index=True, tracking=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    state = fields.Selection(
        [
            ("open", "Open"),
            ("reviewed", "Reviewed"),
            ("exception", "Exception"),
        ],
        default="open",
        required=True,
        tracking=True,
        index=True,
    )
    unreconciled_bank_count = fields.Integer(readonly=True)
    pending_bank_candidate_count = fields.Integer(readonly=True)
    draft_invoice_count = fields.Integer(readonly=True)
    sale_to_invoice_count = fields.Integer(readonly=True)
    open_quotation_count = fields.Integer(readonly=True)
    open_service_task_count = fields.Integer(readonly=True)
    actual_open_crm_count = fields.Integer(readonly=True)
    stale_crm_count = fields.Integer(readonly=True)
    imported_reference_count = fields.Integer(readonly=True)
    overdue_activity_count = fields.Integer(readonly=True)
    open_product_issue_count = fields.Integer(readonly=True)
    product_live_fix_count = fields.Integer(readonly=True)
    product_ready_count = fields.Integer(readonly=True)
    product_blocker_count = fields.Integer(readonly=True)
    product_automation_failure_count = fields.Integer(readonly=True)
    equipment_review_count = fields.Integer(readonly=True)
    contact_match_review_count = fields.Integer(readonly=True)
    last_refreshed_at = fields.Datetime(readonly=True)
    review_note = fields.Text(tracking=True)

    _unique_company_date = models.Constraint(
        "unique(company_id, control_date)",
        "A daily operations control already exists for this company and date.",
    )

    @api.depends("control_date", "company_id")
    def _compute_name(self):
        for control in self:
            control.name = _("%(company)s Operations Control - %(date)s") % {
                "company": control.company_id.display_name,
                "date": control.control_date,
            }

    def action_refresh_counts(self):
        today = fields.Date.context_today(self)
        stale_before = today - timedelta(days=14)
        failure_since = fields.Datetime.to_string(datetime.combine(today, time.min))
        for control in self:
            company_domain = [("company_id", "=", control.company_id.id)]
            values = {
                "unreconciled_bank_count": self.env["account.bank.statement.line"].search_count(
                    company_domain + [("is_reconciled", "=", False)]
                ),
                "pending_bank_candidate_count": self.env[
                    "southern.bank.coding.candidate"
                ].search_count(
                    company_domain + [("state", "in", ["pending", "approved"])]
                ),
                "draft_invoice_count": self.env["account.move"].search_count(
                    company_domain + [("move_type", "=", "out_invoice"), ("state", "=", "draft")]
                ),
                "sale_to_invoice_count": self.env["sale.order"].search_count(
                    company_domain
                    + [("state", "in", ["sale", "done"]), ("invoice_status", "=", "to invoice")]
                ),
                "open_quotation_count": self.env["sale.order"].search_count(
                    company_domain + [("state", "in", ["draft", "sent"])]
                ),
                "open_service_task_count": self.env["project.task"].search_count(
                    [
                        ("active", "=", True),
                        ("stage_id.fold", "=", False),
                        ("company_id", "=", control.company_id.id),
                    ]
                ),
                "actual_open_crm_count": self.env["crm.lead"].with_context(
                    active_test=False
                ).search_count(
                    [
                        ("active", "=", True),
                        ("company_id", "=", control.company_id.id),
                        ("southern_record_class", "=", "actual_opportunity"),
                        ("probability", "<", 100),
                    ]
                ),
                "stale_crm_count": self.env["crm.lead"].with_context(
                    active_test=False
                ).search_count(
                    [
                        ("active", "=", True),
                        ("company_id", "=", control.company_id.id),
                        ("southern_record_class", "=", "actual_opportunity"),
                        ("probability", "<", 100),
                        ("write_date", "<", fields.Date.to_string(stale_before)),
                    ]
                ),
                "imported_reference_count": self.env["crm.lead"].with_context(
                    active_test=False
                ).search_count(
                    [
                        ("southern_record_class", "=", "imported_reference"),
                        ("company_id", "=", control.company_id.id),
                    ]
                ),
                "overdue_activity_count": control._company_overdue_activity_count(today),
                "open_product_issue_count": self.env[
                    "southern.product.quality.issue"
                ].search_count(
                    company_domain
                    + [
                        ("state", "in", ["open", "in_progress", "blocked"]),
                        ("issue_type", "!=", "publication_ready"),
                    ]
                ),
                "product_live_fix_count": self.env[
                    "southern.product.quality.issue"
                ].search_count(
                    company_domain
                    + [
                        ("state", "in", ["open", "in_progress", "blocked"]),
                        ("issue_type", "!=", "publication_ready"),
                        ("product_published", "=", True),
                    ]
                ),
                "product_ready_count": self.env[
                    "southern.product.quality.issue"
                ].search_count(
                    company_domain
                    + [
                        ("state", "in", ["open", "in_progress", "blocked"]),
                        ("issue_type", "=", "publication_ready"),
                    ]
                ),
                "product_blocker_count": self.env[
                    "southern.product.quality.issue"
                ].search_count(
                    company_domain
                    + [
                        ("state", "in", ["open", "in_progress", "blocked"]),
                        ("severity", "=", "4_blocker"),
                    ]
                ),
                "product_automation_failure_count": self.env[
                    "southern.parts.automation.run"
                ].search_count(
                    company_domain
                    + [
                        ("state", "in", ["failed", "blocked"]),
                        ("started_at", ">=", failure_since),
                    ]
                ),
                "equipment_review_count": self.env[
                    "southern.equipment.discovery.candidate"
                ].search_count(
                    company_domain
                    + [("state", "in", ["new", "needs_review", "verification"])]
                ),
                "contact_match_review_count": self.env[
                    "southern.contact.import.line"
                ].search_count(
                    [
                        ("decision", "=", "review"),
                        ("batch_id.company_id", "=", control.company_id.id),
                    ]
                ),
                "last_refreshed_at": fields.Datetime.now(),
            }
            control.write(values)
        return True

    def _company_overdue_activity_count(self, today):
        self.ensure_one()
        total = 0
        Activity = self.env["mail.activity"]
        model_names = (
            "account.move",
            "sale.order",
            "project.task",
            "crm.lead",
            "southern.product.quality.issue",
            "southern.equipment.discovery.candidate",
        )
        for model_name in model_names:
            if model_name not in self.env.registry.models:
                continue
            model = self.env[model_name]
            if "company_id" not in model._fields:
                continue
            record_ids = model.search([("company_id", "=", self.company_id.id)]).ids
            for offset in range(0, len(record_ids), 1000):
                total += Activity.search_count(
                    [
                        ("date_deadline", "<", fields.Date.to_string(today)),
                        ("res_model", "=", model_name),
                        ("res_id", "in", record_ids[offset : offset + 1000]),
                    ]
                )
        return total

    @api.model
    def cron_refresh_daily_controls(self):
        today = fields.Date.context_today(self)
        for company in self.env["res.company"].search([]):
            control = self.search(
                [("company_id", "=", company.id), ("control_date", "=", today)], limit=1
            )
            if not control:
                control = self.create({"company_id": company.id, "control_date": today})
            control.action_refresh_counts()

    def action_mark_reviewed(self):
        self.write({"state": "reviewed"})

    def action_mark_exception(self):
        self.write({"state": "exception"})

    def action_reopen(self):
        self.write({"state": "open"})
