from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


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
    southern_sale_order_id = fields.Many2one(
        "sale.order",
        string="Quotation / Sales Order",
        tracking=True,
        copy=False,
        index=True,
        ondelete="set null",
    )
    southern_quote_line_ids = fields.One2many(
        related="southern_sale_order_id.order_line",
        string="Quotation Lines",
        readonly=False,
    )
    southern_sale_state = fields.Selection(
        related="southern_sale_order_id.state",
        string="Sales Status",
        readonly=True,
    )
    southern_sale_currency_id = fields.Many2one(
        related="southern_sale_order_id.currency_id",
        string="Quote Currency",
        readonly=True,
    )
    southern_sale_amount_total = fields.Monetary(
        related="southern_sale_order_id.amount_total",
        currency_field="southern_sale_currency_id",
        string="Quote Total",
        readonly=True,
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
            task.southern_sale_order_id = case.sale_order_id

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

    def _southern_prepare_case_from_task(self):
        self.ensure_one()
        technician = self.user_ids[:1]
        complaint = self.description or self.name
        return {
            "service_domain": "customer",
            "partner_id": self.partner_id.id,
            "client_equipment_id": self.southern_client_equipment_id.id,
            "service_location": "onsite",
            "service_title": self.name,
            "equipment_description": self.dmc_equipment,
            "serial_number": self.dmc_serial_number,
            "equipment_run_hours": self.dmc_equipment_run_hours,
            "complaint": complaint,
            "advisor_id": self.env.user.id,
            "technician_id": technician.id,
            "scheduled_start": self.planned_date_begin,
            "estimated_hours": self.allocated_hours or 1.0,
            "commercial_basis": (
                "warranty" if self.under_warranty else "estimate"
            ),
        }

    def _southern_get_or_create_sale_order(self):
        self.ensure_one()
        order = (
            self.southern_sale_order_id
            or self.southern_service_case_id.sale_order_id
            or self.sale_order_id
        )
        if order:
            if not self.southern_sale_order_id:
                self.southern_sale_order_id = order
            return order
        if not self.partner_id:
            raise ValidationError(
                _("Select a Customer before creating the quotation.")
            )
        if not self.dmc_equipment:
            raise ValidationError(
                _("Enter the Equipment description before creating the quotation.")
            )
        if not self.dmc_serial_number:
            raise ValidationError(
                _("Enter the Serial Number before creating the quotation.")
            )

        case = self.southern_service_case_id
        if not case:
            case = self.env["southern.service.case"].create(
                self._southern_prepare_case_from_task()
            )
            self.southern_service_case_id = case
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner_id.id,
                "origin": self.name,
                "southern_quote_type": "service",
                "southern_service_location": case.service_location,
                "southern_client_equipment_id": (
                    self.southern_client_equipment_id.id
                ),
                "southern_service_title": self.name,
                "southern_equipment_description": self.dmc_equipment,
                "southern_serial_number": self.dmc_serial_number,
                "southern_equipment_run_hours": self.dmc_equipment_run_hours,
                "southern_service_request": case.complaint,
                "southern_commercial_basis": case.commercial_basis,
                "southern_technician_id": case.technician_id.id,
                "southern_scheduled_start": case.scheduled_start,
                "southern_estimated_hours": case.estimated_hours,
                "southern_service_case_id": case.id,
            }
        )
        case.sale_order_id = order
        self.southern_sale_order_id = order
        return order

    def action_southern_create_quotation(self):
        self._southern_get_or_create_sale_order()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_southern_open_sale_order(self):
        self.ensure_one()
        order = self._southern_get_or_create_sale_order()
        return {
            "type": "ir.actions.act_window",
            "name": _("Quotation / Sales Order"),
            "res_model": "sale.order",
            "view_mode": "form",
            "res_id": order.id,
        }

    def action_southern_send_quotation(self):
        self.ensure_one()
        return self._southern_get_or_create_sale_order().action_quotation_send()

    def action_southern_confirm_sale_order(self):
        self.ensure_one()
        self._southern_get_or_create_sale_order().action_confirm()
        return {"type": "ir.actions.client", "tag": "reload"}

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
                vals.setdefault("southern_sale_order_id", case.sale_order_id.id)
            if equipment:
                vals.setdefault("dmc_equipment", equipment.name)
                vals.setdefault(
                    "dmc_serial_number",
                    equipment.serial_no or "Unserialized",
                )
        return super().create(vals_list)
