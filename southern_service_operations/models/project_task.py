from odoo import api, fields, models


class ProjectTask(models.Model):
    _inherit = "project.task"

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
        string="Client Equipment",
        tracking=True,
        index=True,
        ondelete="restrict",
    )

    @api.onchange("southern_service_case_id")
    def _onchange_southern_service_case_id(self):
        for task in self:
            case = task.southern_service_case_id
            if not case:
                continue
            task.partner_id = case.partner_id
            task.southern_client_equipment_id = case.client_equipment_id
            task.dmc_equipment = case.equipment_description
            task.dmc_serial_number = case.serial_number
            task.dmc_equipment_run_hours = case.equipment_run_hours

    @api.onchange("southern_client_equipment_id")
    def _onchange_southern_client_equipment_id(self):
        for task in self:
            task._southern_sync_equipment_display()

    def _southern_sync_equipment_display(self):
        for task in self:
            equipment = task.southern_client_equipment_id
            if not equipment:
                continue
            task.dmc_equipment = equipment.name
            task.dmc_serial_number = equipment.serial_no or "Unserialized"
            if equipment.client:
                task.partner_id = equipment.client

    @api.model_create_multi
    def create(self, vals_list):
        Case = self.env["southern.service.case"]
        Equipment = self.env["equipment.details"]
        SaleLine = self.env["sale.order.line"]
        for vals in vals_list:
            case = (
                Case.browse(vals["southern_service_case_id"])
                if vals.get("southern_service_case_id")
                else Case
            )
            sale_line = (
                SaleLine.browse(vals["sale_line_id"])
                if vals.get("sale_line_id")
                else SaleLine
            )
            service_order = (
                sale_line.order_id
                if sale_line
                and sale_line.order_id.southern_quote_type == "service"
                else self.env["sale.order"]
            )
            if not case and service_order:
                case = service_order.southern_service_case_id
                if case:
                    vals.setdefault("southern_service_case_id", case.id)
            equipment = (
                Equipment.browse(vals["southern_client_equipment_id"])
                if vals.get("southern_client_equipment_id")
                else (
                    case.client_equipment_id
                    if case
                    else service_order.southern_client_equipment_id
                )
            )
            if case:
                vals.setdefault("partner_id", case.partner_id.id)
                vals.setdefault(
                    "southern_client_equipment_id", case.client_equipment_id.id
                )
            if equipment:
                vals.setdefault("dmc_equipment", equipment.name)
                vals.setdefault(
                    "dmc_serial_number",
                    equipment.serial_no or "Unserialized",
                )
        return super().create(vals_list)
