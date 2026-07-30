from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class SouthernServiceCase(models.Model):
    _name = "southern.service.case"
    _description = "Southern Service Case"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, requested_date asc, id desc"

    name = fields.Char(
        string="Case Number",
        required=True,
        readonly=True,
        copy=False,
        default=lambda self: _("New"),
        index=True,
        tracking=True,
    )
    active = fields.Boolean(default=True)
    service_domain = fields.Selection(
        [
            ("customer", "Customer"),
            ("internal", "Our Equipment"),
        ],
        string="Service For",
        required=True,
        default="customer",
        tracking=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        tracking=True,
        index=True,
    )
    client_equipment_id = fields.Many2one(
        "equipment.details",
        string="Equipment",
        tracking=True,
        index=True,
        ondelete="restrict",
    )
    maintenance_equipment_id = fields.Many2one(
        "maintenance.equipment",
        string="Internal Equipment",
        tracking=True,
        index=True,
        ondelete="restrict",
    )
    service_location = fields.Selection(
        [
            ("onsite", "On-site"),
            ("shop", "Shop"),
            ("hybrid", "On-site and Shop"),
            ("internal", "Internal"),
        ],
        string="Work Location",
        required=True,
        default="onsite",
        tracking=True,
    )
    complaint = fields.Text(string="Request / Complaint", required=True, tracking=True)
    advisor_id = fields.Many2one(
        "res.users",
        string="Service Coordinator",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        domain=[("share", "=", False)],
    )
    priority = fields.Selection(
        [
            ("0", "Normal"),
            ("1", "Low"),
            ("2", "High"),
            ("3", "Urgent"),
        ],
        default="0",
        required=True,
        tracking=True,
    )
    requested_date = fields.Datetime(string="Requested Date", tracking=True)
    technician_id = fields.Many2one(
        "res.users",
        string="Technician",
        tracking=True,
        domain=[("share", "=", False), ("active", "=", True)],
    )
    scheduled_start = fields.Datetime(string="Scheduled Start", tracking=True)
    estimated_hours = fields.Float(
        string="Estimated Hours",
        tracking=True,
        default=1.0,
    )
    commercial_basis = fields.Selection(
        [
            ("estimate", "Estimate Required"),
            ("preauthorized", "Pre-authorized"),
            ("warranty", "Warranty"),
            ("contract", "Contract"),
            ("no_charge", "No Charge"),
            ("internal", "Internal"),
        ],
        string="Authorization",
        required=True,
        default="estimate",
        tracking=True,
    )
    exception_reason = fields.Text(
        string="Equipment / Authorization Exception",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("intake", "Intake"),
            ("diagnosing", "Diagnosing"),
            ("estimating", "Estimating"),
            ("waiting_customer", "Waiting for Customer"),
            ("ready", "Ready"),
            ("scheduled", "Scheduled"),
            ("in_progress", "In Progress"),
            ("waiting_parts", "Waiting for Parts"),
            ("work_complete", "Work Complete"),
            ("ready_invoice", "Ready to Invoice"),
            ("invoiced", "Invoiced"),
            ("closed", "Closed"),
            ("cancelled", "Cancelled"),
        ],
        default="intake",
        required=True,
        tracking=True,
        index=True,
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Quotation / Sales Order",
        tracking=True,
        copy=False,
        ondelete="set null",
    )
    task_ids = fields.One2many(
        "project.task",
        "southern_service_case_id",
        string="Scheduled Work",
    )
    repair_order_ids = fields.One2many(
        "repair.order",
        "southern_service_case_id",
        string="Shop Work",
    )
    maintenance_request_ids = fields.One2many(
        "maintenance.request",
        "southern_service_case_id",
        string="Internal Work",
    )
    task_count = fields.Integer(compute="_compute_counts")
    repair_count = fields.Integer(compute="_compute_counts")
    maintenance_count = fields.Integer(compute="_compute_counts")
    purchase_count = fields.Integer(compute="_compute_counts")
    invoice_count = fields.Integer(related="sale_order_id.invoice_count")

    _name_unique = models.Constraint(
        "unique(name)",
        "The Service Case number must be unique.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southern.service.case"
                ) or _("New")
        return super().create(vals_list)

    @api.depends("task_ids", "repair_order_ids", "maintenance_request_ids")
    def _compute_counts(self):
        PurchaseLine = self.env["purchase.order.line"].sudo()
        for case in self:
            case.task_count = len(case.task_ids)
            case.repair_count = len(case.repair_order_ids)
            case.maintenance_count = len(case.maintenance_request_ids)
            case.purchase_count = PurchaseLine.search_count(
                [("southern_service_case_id", "=", case.id)]
            )

    @api.onchange("service_domain")
    def _onchange_service_domain(self):
        for case in self:
            if case.service_domain == "internal":
                case.partner_id = False
                case.client_equipment_id = False
                case.service_location = "internal"
                case.commercial_basis = "internal"
            elif case.service_location == "internal":
                case.maintenance_equipment_id = False
                case.service_location = "onsite"
                case.commercial_basis = "estimate"

    @api.onchange("client_equipment_id")
    def _onchange_client_equipment_id(self):
        for case in self:
            equipment = case.client_equipment_id
            if equipment and equipment.client:
                case.partner_id = equipment.client

    @api.constrains(
        "service_domain",
        "partner_id",
        "client_equipment_id",
        "maintenance_equipment_id",
        "service_location",
        "commercial_basis",
    )
    def _check_service_identity(self):
        for case in self:
            if case.service_domain == "internal":
                if not case.maintenance_equipment_id:
                    raise ValidationError(
                        _("Internal Service requires Internal Equipment.")
                    )
                if case.client_equipment_id or case.partner_id:
                    raise ValidationError(
                        _("Internal Service cannot use customer or Client Equipment.")
                    )
                if case.service_location != "internal":
                    raise ValidationError(
                        _("Internal Service must use the Internal work location.")
                    )
                continue

            if not case.partner_id:
                raise ValidationError(_("Customer Service requires a customer."))
            if not case.client_equipment_id and not case.exception_reason:
                raise ValidationError(
                    _(
                        "Customer Service requires Equipment or a documented "
                        "equipment exception."
                    )
                )
            case._validate_equipment_customer()
            if case.service_location == "internal":
                raise ValidationError(
                    _("Customer Service cannot use the Internal work location.")
                )
            if case.commercial_basis == "internal":
                raise ValidationError(
                    _("Customer Service cannot use Internal authorization.")
                )

    def _validate_equipment_customer(self):
        for case in self:
            equipment = case.client_equipment_id
            if not equipment:
                continue
            equipment._southern_validate_service_identity()
            if not equipment.client:
                raise ValidationError(
                    _("Client Equipment must be tied to a customer contact.")
                )
            if not case.partner_id:
                continue
            if (
                equipment.client.commercial_partner_id
                != case.partner_id.commercial_partner_id
            ):
                raise ValidationError(
                    _(
                        "The selected Equipment belongs to %(equipment_customer)s, "
                        "not %(case_customer)s."
                    )
                    % {
                        "equipment_customer": equipment.client.display_name,
                        "case_customer": case.partner_id.display_name,
                    }
                )

    def _get_fsm_project(self):
        project_fields = self.env["project.project"]._fields
        domain = [("is_fsm", "=", True)] if "is_fsm" in project_fields else []
        project = self.env["project.project"].search(domain, limit=1)
        if not project:
            raise UserError(
                _(
                    "No Field Service project is configured. Configure one before "
                    "routing on-site Service."
                )
            )
        return project

    def _prepare_task_values(self):
        self.ensure_one()
        equipment = self.client_equipment_id
        equipment_name = (
            equipment.name
            or _("Equipment exception: %s") % self.exception_reason
        )
        serial_display = equipment.serial_no or _("Unserialized")
        return {
            "name": self.name,
            "partner_id": self.partner_id.id,
            "project_id": self._get_fsm_project().id,
            "description": self.complaint,
            "user_ids": (
                [Command.set(self.technician_id.ids)]
                if self.technician_id
                else []
            ),
            "planned_date_begin": self.scheduled_start,
            "allocated_hours": self.estimated_hours,
            "southern_service_case_id": self.id,
            "southern_client_equipment_id": equipment.id,
            "dmc_equipment": equipment_name,
            "dmc_serial_number": serial_display,
        }

    def _prepare_repair_values(self):
        self.ensure_one()
        equipment = self.client_equipment_id
        if not equipment or not equipment.product_id:
            raise UserError(
                _(
                    "Shop Service requires Equipment linked to an Odoo Product "
                    "before a Repair Order can be created."
                )
            )
        return {
            "partner_id": self.partner_id.id,
            "product_id": equipment.product_id.id,
            "internal_notes": self.complaint,
            "under_warranty": self.commercial_basis == "warranty",
            "user_id": (self.technician_id or self.advisor_id).id,
            "southern_service_case_id": self.id,
            "southern_client_equipment_id": equipment.id,
            "sale_order_id": self.sale_order_id.id,
        }

    def _prepare_maintenance_values(self):
        self.ensure_one()
        return {
            "name": self.complaint,
            "equipment_id": self.maintenance_equipment_id.id,
            "maintenance_type": "corrective",
            "owner_user_id": self.advisor_id.id,
            "user_id": self.advisor_id.id,
            "southern_service_case_id": self.id,
        }

    def action_route_work(self):
        for case in self:
            case._check_service_identity()
            if case.service_domain == "internal":
                if not case.maintenance_request_ids:
                    self.env["maintenance.request"].create(
                        case._prepare_maintenance_values()
                    )
                case.state = "ready"
                continue

            if case.service_location in ("onsite", "hybrid") and not case.task_ids:
                self.env["project.task"].create(case._prepare_task_values())
            if (
                case.service_location in ("shop", "hybrid")
                and not case.repair_order_ids
            ):
                self.env["repair.order"].create(case._prepare_repair_values())
            task_values = {
                "description": case.complaint,
                "user_ids": [Command.set(case.technician_id.ids)],
                "planned_date_begin": case.scheduled_start,
                "allocated_hours": case.estimated_hours,
            }
            if case.task_ids:
                case.task_ids.write(task_values)
            if case.repair_order_ids:
                case.repair_order_ids.write(
                    {
                        "internal_notes": case.complaint,
                        "user_id": (case.technician_id or case.advisor_id).id,
                    }
                )
            case.state = (
                "scheduled"
                if case.technician_id and case.scheduled_start
                else "ready"
            )
        return True

    def action_create_quotation(self):
        self.ensure_one()
        if self.service_domain != "customer":
            raise UserError(_("Internal Service does not create customer quotations."))
        if self.sale_order_id:
            return self.action_view_sale_order()
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner_id.id,
                "origin": self.name,
                "southern_quote_type": "service",
                "southern_service_location": self.service_location,
                "southern_client_equipment_id": self.client_equipment_id.id,
                "southern_equipment_exception_reason": self.exception_reason,
                "southern_service_request": self.complaint,
                "southern_commercial_basis": self.commercial_basis,
                "southern_technician_id": self.technician_id.id,
                "southern_scheduled_start": self.scheduled_start,
                "southern_estimated_hours": self.estimated_hours,
                "southern_service_case_id": self.id,
                "order_line": [
                    Command.create(
                        {
                            "display_type": "line_section",
                            "name": _("%(case)s — %(equipment)s")
                            % {
                                "case": self.name,
                                "equipment": (
                                    self.client_equipment_id.display_name
                                    if self.client_equipment_id
                                    else _("Equipment exception")
                                ),
                            },
                        }
                    ),
                    Command.create(
                        {
                            "display_type": "line_note",
                            "name": self.complaint,
                        }
                    ),
                ],
            }
        )
        self.sale_order_id = order
        return self.action_view_sale_order()

    def action_view_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return self.action_create_quotation()
        return {
            "type": "ir.actions.act_window",
            "name": _("Quotation / Sales Order"),
            "res_model": "sale.order",
            "view_mode": "form",
            "res_id": self.sale_order_id.id,
            "context": {"form_view_initial_mode": "edit"},
        }

    def action_view_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Scheduled Work"),
            "res_model": "project.task",
            "view_mode": "kanban,list,form",
            "domain": [("southern_service_case_id", "=", self.id)],
            "context": {
                "default_southern_service_case_id": self.id,
                "default_partner_id": self.partner_id.id,
                "default_southern_client_equipment_id": self.client_equipment_id.id,
            },
        }

    def action_view_repairs(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Shop Work"),
            "res_model": "repair.order",
            "view_mode": "list,form",
            "domain": [("southern_service_case_id", "=", self.id)],
            "context": {
                "default_southern_service_case_id": self.id,
                "default_partner_id": self.partner_id.id,
                "default_southern_client_equipment_id": self.client_equipment_id.id,
            },
        }

    def action_view_maintenance(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Internal Work"),
            "res_model": "maintenance.request",
            "view_mode": "kanban,list,form",
            "domain": [("southern_service_case_id", "=", self.id)],
            "context": {
                "default_southern_service_case_id": self.id,
                "default_equipment_id": self.maintenance_equipment_id.id,
            },
        }

    def action_view_purchases(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Purchase Tracking"),
            "res_model": "purchase.order",
            "view_mode": "list,form",
            "domain": [
                ("order_line.southern_service_case_id", "=", self.id),
            ],
        }

    def action_set_diagnosing(self):
        self.write({"state": "diagnosing"})

    def action_set_waiting_customer(self):
        self.write({"state": "waiting_customer"})

    def action_set_in_progress(self):
        self.write({"state": "in_progress"})

    def action_set_waiting_parts(self):
        self.write({"state": "waiting_parts"})

    def action_set_work_complete(self):
        self.write({"state": "work_complete"})

    def action_set_ready_invoice(self):
        for case in self:
            if case.service_domain != "customer" or not case.sale_order_id:
                raise UserError(
                    _("Ready to Invoice requires customer Service and a Sales Order.")
                )
        self.write({"state": "ready_invoice"})

    def action_close(self):
        self.write({"state": "closed"})

    def action_cancel(self):
        self.write({"state": "cancelled"})
