from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SouthernServiceInspectionItem(models.Model):
    _name = "southern.service.inspection.item"
    _description = "Southern Equipment Inspection Item"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    task_id = fields.Many2one(
        "project.task",
        string="Service Job",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="task_id.company_id",
        store=True,
        readonly=True,
    )
    inspection_area = fields.Char(
        string="System / Area",
        required=True,
        help="Examples: Engine, Hydraulics, Electrical, Safety, or Attachments.",
    )
    name = fields.Char(string="Inspection Item", required=True)
    result = fields.Selection(
        [
            ("pass", "Pass"),
            ("monitor", "Monitor"),
            ("repair", "Repair Needed"),
            ("not_inspected", "Not Inspected"),
        ],
        required=True,
        default="not_inspected",
        index=True,
    )
    priority = fields.Selection(
        [
            ("low", "Low"),
            ("medium", "Medium"),
            ("high", "High"),
            ("critical", "Critical"),
        ],
        required=True,
        default="medium",
    )
    measurement = fields.Char(
        help="Record the observed value, pressure, voltage, wear, or other measurement."
    )
    fault_code = fields.Char(string="Fault / Diagnostic Code")
    finding = fields.Text(
        help="Record the technician's observation without assuming an unverified cause."
    )
    recommended_action = fields.Text(
        help="Record the inspection-based recommendation for technician review."
    )
    include_in_ai = fields.Boolean(
        string="Use for AI Estimate",
        default=True,
        help="Send this written inspection item to the Southern Equipment AI estimate review.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        items = super().create(vals_list)
        tasks = items.task_id.filtered(
            lambda task: task.southern_inspection_state == "not_started"
        )
        if tasks:
            tasks.write(
                {
                    "southern_inspection_state": "in_progress",
                    "southern_inspection_started_at": fields.Datetime.now(),
                    "southern_inspector_id": self.env.user.id,
                }
            )
        return items


class ProjectTaskDigitalEquipmentInspection(models.Model):
    _inherit = "project.task"

    southern_inspection_state = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("in_progress", "In Progress"),
            ("completed", "Completed"),
        ],
        string="Inspection Status",
        default="not_started",
        required=True,
        tracking=True,
        copy=False,
    )
    southern_inspection_started_at = fields.Datetime(
        string="Inspection Started",
        readonly=True,
        copy=False,
    )
    southern_inspection_completed_at = fields.Datetime(
        string="Inspection Completed",
        readonly=True,
        copy=False,
    )
    southern_inspector_id = fields.Many2one(
        "res.users",
        string="Inspector",
        readonly=True,
        copy=False,
    )
    southern_inspection_summary = fields.Text(
        string="Inspection Summary",
        tracking=True,
        help="Summarize the overall equipment condition and important limitations.",
    )
    southern_inspection_item_ids = fields.One2many(
        "southern.service.inspection.item",
        "task_id",
        string="Digital Equipment Inspection",
        copy=True,
    )
    southern_inspection_attention_count = fields.Integer(
        string="Items Requiring Attention",
        compute="_compute_southern_inspection_counts",
    )

    @api.depends("southern_inspection_item_ids.result")
    def _compute_southern_inspection_counts(self):
        for task in self:
            task.southern_inspection_attention_count = len(
                task.southern_inspection_item_ids.filtered(
                    lambda item: item.result in ("monitor", "repair")
                )
            )

    def action_southern_start_inspection(self):
        for task in self:
            if not task.is_fsm:
                raise ValidationError(
                    _("Digital Equipment Inspections are available only on Service Jobs.")
                )
            values = {"southern_inspection_state": "in_progress"}
            if not task.southern_inspection_started_at:
                values["southern_inspection_started_at"] = fields.Datetime.now()
            if not task.southern_inspector_id:
                values["southern_inspector_id"] = self.env.user.id
            task.write(values)
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_southern_complete_inspection(self):
        for task in self:
            if not task.southern_inspection_item_ids:
                raise ValidationError(
                    _("Add at least one inspection item before completing the inspection.")
                )
            task.write(
                {
                    "southern_inspection_state": "completed",
                    "southern_inspection_completed_at": fields.Datetime.now(),
                    "southern_inspector_id": (
                        task.southern_inspector_id.id or self.env.user.id
                    ),
                }
            )
            task.message_post(
                body=_(
                    "Digital Equipment Inspection completed with %(count)s "
                    "item(s) requiring attention.",
                    count=task.southern_inspection_attention_count,
                )
            )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_southern_reopen_inspection(self):
        self.write(
            {
                "southern_inspection_state": "in_progress",
                "southern_inspection_completed_at": False,
            }
        )
        return {"type": "ir.actions.client", "tag": "reload"}
