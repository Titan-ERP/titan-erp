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
        string="Service Jobs",
    )
    southern_repair_order_ids = fields.One2many(
        "repair.order",
        "southern_client_equipment_id",
        string="Shop Work",
    )
    southern_service_case_count = fields.Integer(compute="_compute_southern_counts")
    southern_task_count = fields.Integer(compute="_compute_southern_counts")
    southern_repair_count = fields.Integer(compute="_compute_southern_counts")

    @api.model
    def _southern_find_or_create_serialized(
        self,
        partner,
        equipment_name,
        serial_number,
    ):
        """Return the durable customer-equipment record for service intake."""
        partner = partner.exists()
        equipment_name = (equipment_name or "").strip()
        serial_number = (serial_number or "").strip()
        if (
            not partner
            or not equipment_name
            or not serial_number
            or serial_number.casefold() == "unserialized"
        ):
            return self.browse()

        equipment = self.search([("serial_no", "=ilike", serial_number)], limit=1)
        if equipment:
            if (
                equipment.client
                and equipment.client.commercial_partner_id
                != partner.commercial_partner_id
            ):
                raise ValidationError(
                    _(
                        "Serial Number %(serial)s is already assigned to "
                        "%(customer)s."
                    )
                    % {
                        "serial": serial_number,
                        "customer": equipment.client.display_name,
                    }
                )
            if not equipment.client:
                equipment.client = partner
            return equipment

        return self.create(
            {
                "name": equipment_name,
                "client": partner.id,
                "model": equipment_name,
                "serial_no": serial_number,
                "southern_active": True,
            }
        )

    def _southern_ai_service_history(self, current_task=None, limit=12):
        """Return concise, structured history for an AI estimate prompt."""
        self.ensure_one()
        current_case = current_task.southern_service_case_id if current_task else False
        history = []
        for case in self.southern_service_case_ids.filtered(
            lambda row: row != current_case
        ):
            history.append(
                {
                    "record_type": "service_case",
                    "record": case.name,
                    "date": fields.Datetime.to_string(
                        case.requested_date or case.create_date
                    ),
                    "title": case.service_title or "",
                    "state": case.state,
                    "run_hours": case.equipment_run_hours,
                    "complaint": case.complaint or "",
                    "diagnosis": case.diagnosis or "",
                    "work_performed": case.work_performed or "",
                    "recommendations": case.recommendations or "",
                }
            )
        for task in self.southern_task_ids.filtered(
            lambda row: row != current_task and not row.southern_service_case_id
        ):
            history.append(
                {
                    "record_type": "service_job",
                    "record": task.display_name,
                    "date": fields.Datetime.to_string(task.create_date),
                    "title": task.name or "",
                    "state": task.stage_id.display_name or "",
                    "run_hours": task.dmc_equipment_run_hours,
                    "complaint": task.description or "",
                    "diagnosis": task.southern_diagnosis or "",
                    "work_performed": task.southern_work_performed or "",
                    "recommendations": task.southern_recommendations or "",
                }
            )
        history.sort(key=lambda row: row["date"] or "", reverse=True)
        return history[:limit]

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
            "name": "Service Jobs",
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
