from odoo import fields, models


class MaintenanceRequest(models.Model):
    _inherit = "maintenance.request"

    southern_service_case_id = fields.Many2one(
        "southern.service.case",
        string="Service Case",
        tracking=True,
        copy=False,
        index=True,
        ondelete="set null",
    )
    southern_waiting_parts = fields.Boolean(
        string="Waiting for Parts",
        tracking=True,
    )

    def action_view_southern_service_case(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Service Case",
            "res_model": "southern.service.case",
            "view_mode": "form",
            "res_id": self.southern_service_case_id.id,
        }


class MaintenanceEquipment(models.Model):
    _inherit = "maintenance.equipment"

    southern_internal_asset = fields.Boolean(
        string="Internal Service Equipment",
        default=True,
        help="Identifies company-owned equipment used by the unified Service app.",
    )
