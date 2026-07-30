from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ClientEquipment(models.Model):
    _inherit = "equipment.details"

    southern_active = fields.Boolean(
        string="Active for Service",
        default=True,
        tracking=True,
    )
    southern_retirement_reason = fields.Text(
        string="Retirement Reason",
        tracking=True,
    )
    southern_unserialized = fields.Boolean(
        string="Unserialized",
        tracking=True,
        help="Use for legitimate equipment that has no manufacturer serial number.",
    )
    southern_site_contact_id = fields.Many2one(
        "res.partner",
        string="Service Site Contact",
        tracking=True,
    )
    southern_service_case_ids = fields.One2many(
        "southern.service.case",
        "client_equipment_id",
        string="Service Cases",
    )
    southern_task_ids = fields.One2many(
        "project.task",
        "southern_client_equipment_id",
        string="Scheduled Work",
    )
    southern_repair_order_ids = fields.One2many(
        "repair.order",
        "southern_client_equipment_id",
        string="Shop Work",
    )
    southern_service_case_count = fields.Integer(compute="_compute_southern_counts")
    southern_task_count = fields.Integer(compute="_compute_southern_counts")
    southern_repair_count = fields.Integer(compute="_compute_southern_counts")

    @api.depends(
        "southern_service_case_ids",
        "southern_task_ids",
        "southern_repair_order_ids",
    )
    def _compute_southern_counts(self):
        for equipment in self:
            equipment.southern_service_case_count = len(
                equipment.southern_service_case_ids
            )
            equipment.southern_task_count = len(equipment.southern_task_ids)
            equipment.southern_repair_count = len(
                equipment.southern_repair_order_ids
            )

    def _southern_validate_service_identity(self):
        for equipment in self:
            if not equipment.serial_no and not equipment.southern_unserialized:
                raise ValidationError(
                    _(
                        "%(equipment)s needs a serial number or the explicit "
                        "Unserialized flag before Service work can be routed."
                    )
                    % {"equipment": equipment.display_name}
                )
        return True

    def action_view_southern_service_cases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Service Cases",
            "res_model": "southern.service.case",
            "view_mode": "list,form",
            "domain": [("client_equipment_id", "=", self.id)],
            "context": {
                "default_client_equipment_id": self.id,
                "default_partner_id": self.client.id,
            },
        }

    def action_view_southern_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Scheduled Work",
            "res_model": "project.task",
            "view_mode": "kanban,list,form",
            "domain": [("southern_client_equipment_id", "=", self.id)],
        }

    def action_view_southern_repairs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Shop Work",
            "res_model": "repair.order",
            "view_mode": "list,form",
            "domain": [("southern_client_equipment_id", "=", self.id)],
        }
