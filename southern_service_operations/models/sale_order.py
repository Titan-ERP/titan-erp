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
    southern_service_title = fields.Char(
        string="Service Job",
        tracking=True,
    )
    southern_equipment_description = fields.Char(
        string="Equipment Description",
        help="Equipment make/model or other clear description captured at intake.",
        tracking=True,
    )
    southern_serial_number = fields.Char(
        string="Serial Number",
        tracking=True,
    )
    southern_equipment_run_hours = fields.Integer(
        string="Equipment Run Hours",
        default=0,
        tracking=True,
    )
    southern_service_request = fields.Text(
        string="Requested Work / Complaint",
        tracking=True,
    )
    southern_commercial_basis = fields.Selection(
        [
            ("estimate", "Estimate Required"),
            ("preauthorized", "Pre-authorized"),
            ("warranty", "Warranty"),
            ("contract", "Contract"),
            ("no_charge", "No Charge"),
        ],
        string="Authorization",
        default="estimate",
        tracking=True,
    )
    southern_technician_id = fields.Many2one(
        "res.users",
        string="Technician",
        tracking=True,
        domain=[("share", "=", False), ("active", "=", True)],
    )
    southern_scheduled_start = fields.Datetime(
        string="Scheduled Start",
        tracking=True,
    )
    southern_estimated_hours = fields.Float(
        string="Estimated Hours",
        tracking=True,
        default=1.0,
    )
    southern_service_case_id = fields.Many2one(
        "southern.service.case",
        string="Service Case",
        tracking=True,
        copy=False,
        index=True,
        ondelete="set null",
    )
    southern_service_state = fields.Selection(
        related="southern_service_case_id.state",
        string="Service Status",
        readonly=True,
    )
    southern_service_task_count = fields.Integer(
        related="southern_service_case_id.task_count",
        string="Service Jobs",
        readonly=True,
    )
    southern_service_repair_count = fields.Integer(
        related="southern_service_case_id.repair_count",
        string="Shop Work",
        readonly=True,
    )
    southern_service_purchase_count = fields.Integer(
        related="southern_service_case_id.purchase_count",
        string="Purchases",
        readonly=True,
    )

    @api.onchange("southern_client_equipment_id")
    def _onchange_southern_client_equipment_id(self):
        for order in self:
            equipment = order.southern_client_equipment_id
            if equipment:
                order.southern_equipment_description = equipment.name
                order.southern_serial_number = (
                    equipment.serial_no or _("Unserialized")
                )
                if equipment.client:
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
            if not order.southern_service_request:
                raise ValidationError(
                    _("Service requires a Requested Work / Complaint description.")
                )
            if not order.southern_service_title:
                raise ValidationError(
                    _("Service requires a Service Job title before confirmation.")
                )
            if not order.southern_equipment_description:
                raise ValidationError(
                    _("Service requires an Equipment Description before confirmation.")
                )
            if not order.southern_serial_number:
                raise ValidationError(
                    _("Service requires a Serial Number before confirmation.")
                )
            if order.southern_equipment_run_hours < 0:
                raise ValidationError(_("Equipment Run Hours cannot be negative."))
            order._check_southern_service_equipment_customer()

    def _prepare_southern_service_case_values(self):
        self.ensure_one()
        complaint = self.southern_service_request or "\n".join(
            line.name for line in self.order_line if line.name
        ) or _("Service requested from %s") % self.name
        return {
            "partner_id": self.partner_id.id,
            "client_equipment_id": self.southern_client_equipment_id.id,
            "service_domain": "customer",
            "service_location": self.southern_service_location,
            "service_title": self.southern_service_title,
            "complaint": complaint,
            "advisor_id": self.user_id.id or self.env.user.id,
            "commercial_basis": self.southern_commercial_basis or "estimate",
            "technician_id": self.southern_technician_id.id,
            "scheduled_start": self.southern_scheduled_start,
            "estimated_hours": self.southern_estimated_hours,
            "equipment_description": self.southern_equipment_description,
            "serial_number": self.southern_serial_number,
            "equipment_run_hours": self.southern_equipment_run_hours,
            "sale_order_id": self.id,
        }

    def _ensure_southern_service_case(self):
        self.ensure_one()
        case = self.southern_service_case_id
        if not case:
            case = self.env["southern.service.case"].create(
                self._prepare_southern_service_case_values()
            )
            self.southern_service_case_id = case
        else:
            values = self._prepare_southern_service_case_values()
            values.pop("sale_order_id", None)
            case.write(values)
            if not case.sale_order_id:
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

    def action_route_southern_service_work(self):
        self.ensure_one()
        if self.southern_quote_type != "service":
            raise ValidationError(
                _("Service Jobs can only be created for Service quotations.")
            )
        self._validate_southern_service_confirmation()
        if not self.southern_technician_id or not self.southern_scheduled_start:
            raise ValidationError(
                _(
                    "Creating a Service Job requires a Technician and "
                    "Scheduled Start."
                )
            )
        if self.southern_estimated_hours <= 0:
            raise ValidationError(
                _(
                    "Creating a Service Job requires Estimated Hours greater "
                    "than zero."
                )
            )
        self._ensure_southern_service_case().action_route_work()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_view_southern_service_tasks(self):
        self.ensure_one()
        return self._ensure_southern_service_case().action_view_tasks()

    def action_view_southern_service_repairs(self):
        self.ensure_one()
        return self._ensure_southern_service_case().action_view_repairs()

    def action_view_southern_service_purchases(self):
        self.ensure_one()
        return self._ensure_southern_service_case().action_view_purchases()


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    southern_service_task_id = fields.Many2one(
        "project.task",
        string="Service Job",
        index=True,
        copy=False,
        ondelete="set null",
    )
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

    @api.onchange("southern_service_task_id")
    def _onchange_southern_service_task_id(self):
        for line in self:
            if (
                line.southern_service_task_id
                and not line.order_id
                and line.southern_service_task_id.southern_sale_order_id
            ):
                line.order_id = (
                    line.southern_service_task_id.southern_sale_order_id
                )

    @api.model_create_multi
    def create(self, vals_list):
        Task = self.env["project.task"]
        Order = self.env["sale.order"]
        for values in vals_list:
            if values.get("southern_service_task_id") or not values.get(
                "order_id"
            ):
                continue
            order = Order.browse(values["order_id"])
            if order.southern_quote_type != "service":
                continue
            task = Task.search(
                [("southern_sale_order_id", "=", order.id)],
                limit=1,
            )
            if task:
                values["southern_service_task_id"] = task.id
        return super().create(vals_list)

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
