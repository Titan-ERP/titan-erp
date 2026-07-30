from odoo import api, fields, models


class RepairOrder(models.Model):
    _inherit = "repair.order"

    southern_service_case_id = fields.Many2one(
        "southern.service.case",
        string="Service Case",
        tracking=True,
        copy=False,
        index=True,
        ondelete="set null",
    )
    southern_client_equipment_id = fields.Many2one(
        "equipment.details",
        string="Equipment",
        tracking=True,
        index=True,
        ondelete="restrict",
    )
    southern_field_task_id = fields.Many2one(
        "project.task",
        string="Related Service Job",
        tracking=True,
        copy=False,
        ondelete="set null",
    )

    @api.onchange("southern_service_case_id")
    def _onchange_southern_service_case_id(self):
        for repair in self:
            case = repair.southern_service_case_id
            if not case:
                continue
            repair.partner_id = case.partner_id
            repair.southern_client_equipment_id = case.client_equipment_id

    @api.onchange("southern_client_equipment_id")
    def _onchange_southern_client_equipment_id(self):
        for repair in self:
            equipment = repair.southern_client_equipment_id
            if not equipment:
                continue
            repair.partner_id = equipment.client
            if equipment.product_id:
                repair.product_id = equipment.product_id

    def action_view_southern_service_case(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Service Case",
            "res_model": "southern.service.case",
            "view_mode": "form",
            "res_id": self.southern_service_case_id.id,
        }
