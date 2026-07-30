from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    southern_quote_type = fields.Selection(
        [
            ("general", "General"),
            ("parts", "Parts"),
            ("service", "Service"),
            ("equipment_sale", "Equipment Sale"),
            ("rental", "Rental"),
        ],
        string="Quotation Type",
        required=True,
        default=lambda self: self.env.context.get(
            "default_southern_quote_type", "general"
        ),
        tracking=True,
        index=True,
    )
    southern_service_location = fields.Selection(
        [
            ("onsite", "On-site"),
            ("shop", "Shop"),
            ("hybrid", "On-site and Shop"),
        ],
        string="Work Location",
        tracking=True,
    )
    southern_client_equipment_id = fields.Many2one(
        "equipment.details",
        string="Equipment",
        tracking=True,
        index=True,
        ondelete="restrict",
    )
    southern_equipment_exception_reason = fields.Text(
        string="Equipment Exception",
        tracking=True,
    )
    southern_service_case_id = fields.Many2one(
        "southern.service.case",
        string="Service Case",
        tracking=True,
        copy=False,
        index=True,
        ondelete="set null",
    )

    @api.onchange("southern_client_equipment_id")
    def _onchange_southern_client_equipment_id(self):
        for order in self:
            equipment = order.southern_client_equipment_id
            if equipment and equipment.client:
                order.partner_id = equipment.client

    @api.onchange("southern_quote_type")
    def _onchange_southern_quote_type(self):
        for order in self:
            if (
                order.southern_quote_type == "service"
                and not order.southern_service_location
            ):
                order.southern_service_location = "onsite"

    @api.constrains(
        "southern_quote_type",
        "southern_service_location",
        "southern_client_equipment_id",
        "partner_id",
    )
    def _check_southern_service_equipment_customer(self):
        for order in self.filtered(
            lambda row: row.southern_quote_type == "service"
        ):
            equipment = order.southern_client_equipment_id
            if equipment:
                equipment._southern_validate_service_identity()
                if not equipment.client:
                    raise ValidationError(
                        _("Client Equipment must be tied to a customer contact.")
                    )
            if (
                equipment
                and equipment.client
                and order.partner_id
                and equipment.client.commercial_partner_id
                != order.partner_id.commercial_partner_id
            ):
                raise ValidationError(
                    _(
                        "The selected Equipment belongs to %(equipment_customer)s, "
                        "not %(order_customer)s."
                    )
                    % {
                        "equipment_customer": equipment.client.display_name,
                        "order_customer": order.partner_id.display_name,
                    }
                )

    def _validate_southern_service_confirmation(self):
        for order in self.filtered(
            lambda row: row.southern_quote_type == "service"
        ):
            if not order.southern_service_location:
                raise ValidationError(_("Service requires a Work Location."))
            if (
                not order.southern_client_equipment_id
                and not order.southern_equipment_exception_reason
            ):
                raise ValidationError(
                    _(
                        "Service requires Equipment or a documented Equipment "
                        "Exception before confirmation."
                    )
                )
            order._check_southern_service_equipment_customer()

    def _prepare_southern_service_case_values(self):
        self.ensure_one()
        complaint = "\n".join(
            line.name for line in self.order_line if line.name
        ) or _("Service requested from %s") % self.name
        return {
            "partner_id": self.partner_id.id,
            "client_equipment_id": self.southern_client_equipment_id.id,
            "service_domain": "customer",
            "service_location": self.southern_service_location,
            "complaint": complaint,
            "advisor_id": self.user_id.id or self.env.user.id,
            "commercial_basis": "estimate",
            "sale_order_id": self.id,
            "exception_reason": self.southern_equipment_exception_reason,
        }

    def _ensure_southern_service_case(self):
        self.ensure_one()
        case = self.southern_service_case_id
        if not case:
            case = self.env["southern.service.case"].create(
                self._prepare_southern_service_case_values()
            )
            self.southern_service_case_id = case
        elif not case.sale_order_id:
            case.sale_order_id = self
        return case

    def action_confirm(self):
        self._validate_southern_service_confirmation()
        service_orders = self.filtered(
            lambda row: row.southern_quote_type == "service"
        )
        cases_by_order = {
            order.id: order._ensure_southern_service_case()
            for order in service_orders
        }
        result = super().action_confirm()
        for order in service_orders:
            case = cases_by_order[order.id]
            existing_tasks = self.env["project.task"].search(
                [
                    ("sale_order_id", "=", order.id),
                    ("southern_service_case_id", "=", False),
                ]
            )
            if existing_tasks:
                existing_tasks.write(
                    {
                        "southern_service_case_id": case.id,
                        "southern_client_equipment_id": (
                            order.southern_client_equipment_id.id
                        ),
                    }
                )
            case.action_route_work()
        return result

    def action_view_southern_service_case(self):
        self.ensure_one()
        case = self._ensure_southern_service_case()
        return {
            "type": "ir.actions.act_window",
            "name": _("Service Case"),
            "res_model": "southern.service.case",
            "view_mode": "form",
            "res_id": case.id,
        }


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    southern_client_equipment_id = fields.Many2one(
        "equipment.details",
        string="Equipment",
        related="order_id.southern_client_equipment_id",
        store=True,
        index=True,
    )
    southern_service_case_id = fields.Many2one(
        "southern.service.case",
        string="Service Case",
        related="order_id.southern_service_case_id",
        store=True,
        index=True,
    )

    def _timesheet_create_task(self, project):
        self.ensure_one()
        case = self.order_id.southern_service_case_id
        if self.order_id.southern_quote_type == "service" and case:
            existing_task = case.task_ids.filtered(
                lambda task: not task.sale_line_id
            )[:1]
            if existing_task:
                existing_task.write(
                    {
                        "sale_line_id": self.id,
                        "sale_order_id": self.order_id.id,
                        "allocated_hours": self._convert_qty_company_hours(
                            existing_task.company_id or self.company_id
                        ),
                    }
                )
                self.task_id = existing_task
                return existing_task
        return super()._timesheet_create_task(project)
