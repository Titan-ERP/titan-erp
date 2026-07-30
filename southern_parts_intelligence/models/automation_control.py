from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SouthernPartsCatalogSync(models.Model):
    _inherit = ["southern.parts.catalog.sync", "mail.thread"]

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    minimum_free_gb = fields.Float(
        default=2.0,
        required=True,
        help="External workers must report at least this much free disk before a run may start.",
    )
    cooldown_until = fields.Datetime(
        readonly=True,
        help="No external source access is permitted before this time.",
    )
    next_allowed_run_at = fields.Datetime(readonly=True)
    approval_state = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("requested", "Requested"),
            ("approved", "Approved"),
            ("rejected", "Rejected"),
        ],
        default="not_required",
        required=True,
        tracking=True,
    )
    approved_by_id = fields.Many2one("res.users", readonly=True)
    approved_at = fields.Datetime(readonly=True)
    approval_note = fields.Text(tracking=True)
    last_external_command_id = fields.Char(readonly=True, index=True)
    last_artifact_uri = fields.Char(readonly=True)
    last_artifact_sha256 = fields.Char(readonly=True)
    run_ids = fields.One2many("southern.parts.automation.run", "sync_id")
    run_record_count = fields.Integer(compute="_compute_run_record_count")

    @api.depends("run_ids")
    def _compute_run_record_count(self):
        for sync in self:
            sync.run_record_count = len(sync.run_ids)

    def action_request_approval(self):
        self.write({"approval_state": "requested"})

    def action_approve(self):
        self.write(
            {
                "approval_state": "approved",
                "approved_by_id": self.env.user.id,
                "approved_at": fields.Datetime.now(),
            }
        )

    def action_reject(self):
        self.write(
            {
                "approval_state": "rejected",
                "approved_by_id": False,
                "approved_at": False,
            }
        )

    def action_view_runs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Automation Runs"),
            "res_model": "southern.parts.automation.run",
            "view_mode": "list,form",
            "domain": [("sync_id", "=", self.id)],
            "context": {"default_sync_id": self.id},
        }

    def assert_external_run_allowed(self, free_gb):
        self.ensure_one()
        now = fields.Datetime.now()
        if free_gb < self.minimum_free_gb:
            raise UserError(
                _("External run blocked: %.2f GB free is below the %.2f GB safety floor.")
                % (free_gb, self.minimum_free_gb)
            )
        if self.cooldown_until and self.cooldown_until > now:
            raise UserError(_("External run blocked until %s.") % self.cooldown_until)
        if self.next_allowed_run_at and self.next_allowed_run_at > now:
            raise UserError(_("The next run is not allowed before %s.") % self.next_allowed_run_at)
        if self.state == "running":
            raise UserError(_("This catalog workflow already has a running batch."))
        if self.approval_state == "requested":
            raise UserError(_("This workflow is waiting for approval."))
        if self.approval_state == "rejected":
            raise UserError(_("This workflow was rejected and must be submitted again."))
        if self.search_count(
            [
                ("id", "!=", self.id),
                ("state", "=", "running"),
                ("mode", "=", self.mode),
            ]
        ):
            raise UserError(_("Another catalog workflow in this mode is already running."))
        return True


class SouthernPartsAutomationRun(models.Model):
    _name = "southern.parts.automation.run"
    _description = "Southern Parts Automation Run"
    _inherit = ["mail.thread"]
    _order = "started_at desc, id desc"

    name = fields.Char(required=True, copy=False, default=lambda self: _("New Run"))
    sync_id = fields.Many2one(
        "southern.parts.catalog.sync",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        "res.company", related="sync_id.company_id", store=True, readonly=True, index=True
    )
    external_run_id = fields.Char(index=True, copy=False)
    command_id = fields.Char(index=True, copy=False)
    worker = fields.Selection(
        [("odoo", "Odoo"), ("aws", "AWS / SSM"), ("codex", "Codex"), ("manual", "Manual")],
        default="manual",
        required=True,
    )
    mode = fields.Selection(
        [
            ("dry_run", "Dry Run"),
            ("evidence_only", "Evidence Only"),
            ("apply", "Apply"),
        ],
        default="dry_run",
        required=True,
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
    started_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    finished_at = fields.Datetime()
    free_gb = fields.Float()
    requested_count = fields.Integer()
    processed_count = fields.Integer()
    changed_count = fields.Integer()
    error_count = fields.Integer()
    http_request_count = fields.Integer()
    slow_page_count = fields.Integer()
    artifact_uri = fields.Char()
    artifact_sha256 = fields.Char()
    artifact_schema_version = fields.Char(default="1.0")
    archive_uri = fields.Char()
    evidence_summary = fields.Text()
    error_message = fields.Text()

    @api.model
    def begin_external_run(self, sync_id, values=None):
        values = dict(values or {})
        sync = self.env["southern.parts.catalog.sync"].browse(sync_id).exists()
        if not sync:
            raise UserError(_("The catalog sync configuration no longer exists."))
        sync.assert_external_run_allowed(float(values.get("free_gb") or 0.0))
        if values.get("mode") == "apply" and sync.approval_state != "approved":
            raise UserError(_("Apply runs require an approved catalog workflow."))
        values.update(
            {
                "sync_id": sync.id,
                "name": values.get("name") or _("External Catalog Run"),
                "state": "running",
                "started_at": fields.Datetime.now(),
            }
        )
        run = self.create(values)
        sync.write(
            {
                "state": "running",
                "last_external_command_id": run.command_id,
                "last_message": _("External run %s started.") % run.display_name,
            }
        )
        return run.id

    def finish_run(self, state, values=None):
        allowed = {"succeeded", "blocked", "failed", "cancelled"}
        if state not in allowed:
            raise UserError(_("Invalid terminal run state: %s") % state)
        for run in self:
            update = dict(values or {})
            update.update({"state": state, "finished_at": fields.Datetime.now()})
            run.write(update)
            sync_values = {
                "state": "error" if state == "failed" else "idle",
                "last_run_at": fields.Datetime.now(),
                "last_message": run.error_message or run.evidence_summary or run.display_name,
                "last_artifact_uri": run.artifact_uri,
                "last_artifact_sha256": run.artifact_sha256,
            }
            if run.mode == "apply":
                sync_values.update(
                    {
                        "approval_state": "not_required",
                        "approved_by_id": False,
                        "approved_at": False,
                    }
                )
            run.sync_id.write(sync_values)
        return True
