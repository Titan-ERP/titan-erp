from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import float_compare


class ProjectTask(models.Model):
    _inherit = "project.task"

    _SOUTHERN_SERVICE_NOTE_MAP = {
        "southern_diagnosis": "diagnosis",
        "southern_work_performed": "work_performed",
        "southern_recommendations": "recommendations",
    }

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
    southern_quote_workflow = fields.Boolean(
        string="Unified Service Quote",
        default=False,
        copy=False,
    )
    southern_quote_line_ids = fields.One2many(
        "sale.order.line",
        "southern_service_task_id",
        string="Quotation Lines",
    )
    southern_service_work_item_ids = fields.One2many(
        "southern.service.work.item",
        "task_id",
        string="Service Tasks",
        copy=True,
    )
    southern_labor_work_item_ids = fields.One2many(
        "southern.service.work.item",
        "task_id",
        string="Service Tasks",
        domain=[("work_type", "=", "labor")],
        copy=True,
    )
    southern_service_task_hours = fields.Float(
        string="Planned Task Hours",
        compute="_compute_southern_service_task_hours",
    )
    southern_equipment_service_count = fields.Integer(
        related="southern_client_equipment_id.southern_service_case_count",
        string="Equipment Service Records",
        readonly=True,
    )
    southern_labor_product_id = fields.Many2one(
        "product.product",
        string="Labor Quote Product",
        domain=[
            ("sale_ok", "=", True),
            ("type", "=", "service"),
        ],
    )
    southern_labor_rate = fields.Monetary(
        string="Labor Rate",
        currency_field="southern_work_currency_id",
        compute="_compute_southern_labor_rate",
    )
    southern_work_currency_id = fields.Many2one(
        "res.currency",
        string="Work Currency",
        compute="_compute_southern_work_currency_id",
    )
    southern_labor_sale_line_id = fields.Many2one(
        "sale.order.line",
        string="Labor Quotation Line",
        readonly=True,
        copy=False,
        ondelete="set null",
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
    southern_diagnosis = fields.Text(string="Diagnosis", tracking=True)
    southern_work_performed = fields.Text(
        string="Work Performed / Resolution",
        tracking=True,
    )
    southern_recommendations = fields.Text(
        string="Recommendations / Follow-up",
        tracking=True,
    )

    @api.depends("southern_labor_work_item_ids.allocated_hours")
    def _compute_southern_service_task_hours(self):
        for task in self:
            task.southern_service_task_hours = sum(
                task.southern_labor_work_item_ids.mapped("allocated_hours")
            )

    @api.depends("southern_labor_product_id.lst_price")
    def _compute_southern_labor_rate(self):
        for task in self:
            task.southern_labor_rate = (
                task.southern_labor_product_id.lst_price
                if task.southern_labor_product_id
                else 0.0
            )

    @api.depends(
        "southern_sale_order_id.currency_id",
        "company_id.currency_id",
    )
    def _compute_southern_work_currency_id(self):
        for task in self:
            task.southern_work_currency_id = (
                task.southern_sale_order_id.currency_id
                or task.company_id.currency_id
            )

    @api.model
    def _southern_default_labor_product(self):
        product_id = self.env["ir.config_parameter"].sudo().get_param(
            "southern_service_operations.default_labor_product_id"
        )
        product = self.env["product.product"]
        if product_id and str(product_id).isdigit():
            product = self.env["product.product"].browse(int(product_id)).exists()
        if product and product.sale_ok and product.type == "service":
            return product
        return self.env["product.product"].search(
            [
                ("name", "=ilike", "Shop Labor"),
                ("sale_ok", "=", True),
                ("type", "=", "service"),
                ("active", "=", True),
            ],
            limit=1,
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
            task.southern_diagnosis = case.diagnosis
            task.southern_work_performed = case.work_performed
            task.southern_recommendations = case.recommendations

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
            "diagnosis": self.southern_diagnosis,
            "work_performed": self.southern_work_performed,
            "recommendations": self.southern_recommendations,
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
                self.with_context(southern_skip_auto_quote=True).write(
                    {"southern_sale_order_id": order.id}
                )
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
            self.with_context(southern_skip_auto_quote=True).write(
                {"southern_service_case_id": case.id}
            )
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
        self.with_context(southern_skip_auto_quote=True).write(
            {"southern_sale_order_id": order.id}
        )
        return order

    def _southern_is_quote_ready(self):
        self.ensure_one()
        return bool(
            self.southern_quote_workflow
            and self.partner_id
            and self.dmc_equipment
            and self.dmc_serial_number
        )

    def _southern_sync_tasks_to_quotation(self, order=None):
        WorkItem = self.env["southern.service.work.item"]
        WorkItem.flush_model(
            [
                "task_id",
                "sequence",
                "name",
                "work_type",
                "allocated_hours",
                "quantity",
                "product_id",
                "unit_price",
                "billable",
            ]
        )
        for task in self:
            task.flush_recordset(["southern_labor_sale_line_id"])
            task.invalidate_recordset(
                [
                    "southern_service_work_item_ids",
                    "southern_labor_sale_line_id",
                ]
            )
            task_order = order or task.southern_sale_order_id
            if not task_order:
                continue
            if task_order.state not in ("draft", "sent"):
                continue
            task_order.order_line.filtered(
                lambda line: not line.southern_service_task_id
            ).write({"southern_service_task_id": task.id})

            work_items = WorkItem.search(
                [("task_id", "=", task.id)],
                order="sequence, id",
            )
            labor_items = work_items.filtered(
                lambda item: item.billable and item.work_type == "labor"
            )

            planned_hours = sum(
                work_items.filtered(
                    lambda item: item.work_type == "labor"
                ).mapped("allocated_hours")
            )
            if planned_hours:
                if float_compare(
                    task.allocated_hours,
                    planned_hours,
                    precision_digits=2,
                ):
                    task.with_context(southern_skip_auto_quote=True).write(
                        {"allocated_hours": planned_hours}
                    )
                if float_compare(
                    task_order.southern_estimated_hours,
                    planned_hours,
                    precision_digits=2,
                ):
                    task_order.write({"southern_estimated_hours": planned_hours})
                case = task.southern_service_case_id
                if case and float_compare(
                    case.estimated_hours,
                    planned_hours,
                    precision_digits=2,
                ):
                    case.write({"estimated_hours": planned_hours})
            labor_line = task.southern_labor_sale_line_id
            if labor_items:
                if not task.southern_labor_product_id:
                    raise ValidationError(
                        _(
                            "Select a Labor Quote Product before saving "
                            "billable labor tasks."
                        )
                    )
                total_hours = sum(labor_items.mapped("allocated_hours"))
                scope = "\n".join(
                    f"- {item.name} ({item.allocated_hours:.2f} hours)"
                    for item in labor_items
                )
                labor_values = {
                    "order_id": task_order.id,
                    "southern_service_task_id": task.id,
                    "product_id": task.southern_labor_product_id.id,
                    "name": _("Service Labor\n%(scope)s") % {"scope": scope},
                    "product_uom_qty": total_hours,
                    "product_uom_id": task.southern_labor_product_id.uom_id.id,
                    "price_unit": task.southern_labor_rate,
                }
                if labor_line:
                    labor_values.pop("order_id")
                    labor_line.write(labor_values)
                else:
                    labor_line = self.env["sale.order.line"].create(labor_values)
                task.with_context(southern_skip_auto_quote=True).write(
                    {"southern_labor_sale_line_id": labor_line.id}
                )
                labor_items.write({"sale_line_id": labor_line.id})
            elif labor_line:
                task.with_context(southern_skip_auto_quote=True).write(
                    {"southern_labor_sale_line_id": False}
                )
                labor_line.unlink()

            work_items.filtered(
                lambda item: (
                    item.work_type == "labor"
                    and not item.billable
                    and item.sale_line_id
                )
            ).write({"sale_line_id": False})
            if labor_line:
                work_items.filtered(
                    lambda item: (
                        item.work_type != "labor"
                        and item.sale_line_id == labor_line
                    )
                ).write({"sale_line_id": False})
            work_items.filtered(
                lambda item: item.work_type != "labor"
            )._sync_individual_to_quotation(task_order)
        return True

    def _southern_auto_prepare_quote(self):
        for task in self:
            if not task._southern_is_quote_ready():
                continue
            order = task._southern_get_or_create_sale_order()
            task._southern_sync_tasks_to_quotation(order)
        return True

    def action_southern_create_quotation(self):
        for task in self:
            order = task._southern_get_or_create_sale_order()
            task._southern_sync_tasks_to_quotation(order)
        return {"type": "ir.actions.client", "tag": "reload"}

    def _southern_action_new_work_item(self, work_type):
        self.ensure_one()
        labels = {
            "labor": _("Add Labor Task"),
            "part": _("Add Part Product"),
            "other": _("Add Other Work"),
        }
        return {
            "type": "ir.actions.act_window",
            "name": labels[work_type],
            "res_model": "southern.service.work.item",
            "view_mode": "form",
            "view_id": self.env.ref(
                "southern_service_operations."
                "view_southern_service_work_item_form"
            ).id,
            "target": "new",
            "context": {
                "default_task_id": self.id,
                "default_work_type": work_type,
                "default_allocated_hours": 0.0 if work_type == "part" else 1.0,
                "default_quantity": 1.0,
                "default_billable": True,
            },
        }

    def action_southern_add_labor_task(self):
        return self._southern_action_new_work_item("labor")

    def action_southern_add_part(self):
        return self._southern_action_new_work_item("part")

    def action_southern_add_other_work(self):
        return self._southern_action_new_work_item("other")

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

    def action_southern_open_equipment_history(self):
        self.ensure_one()
        equipment = self.southern_client_equipment_id
        if not equipment:
            raise ValidationError(
                _("Select or save the Client Equipment before opening history.")
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Equipment Service History"),
            "res_model": "equipment.details",
            "view_mode": "form",
            "res_id": equipment.id,
        }

    def action_southern_send_quotation(self):
        self.ensure_one()
        order = self._southern_get_or_create_sale_order()
        self._southern_sync_tasks_to_quotation(order)
        return order.action_quotation_send()

    def action_southern_confirm_sale_order(self):
        self.ensure_one()
        order = self._southern_get_or_create_sale_order()
        self._southern_sync_tasks_to_quotation(order)
        order.action_confirm()
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
            partner = (
                self.env["res.partner"].browse(vals["partner_id"])
                if vals.get("partner_id")
                else (
                    case.partner_id if case else service_order.partner_id
                )
            )
            is_service_job = bool(
                vals.get(
                    "southern_quote_workflow",
                    self.env.context.get("default_southern_quote_workflow"),
                )
                or case
                or service_order
            )
            if not equipment and is_service_job and partner:
                equipment = Equipment._southern_find_or_create_serialized(
                    partner,
                    vals.get("dmc_equipment"),
                    vals.get("dmc_serial_number"),
                )
                if equipment:
                    vals["southern_client_equipment_id"] = equipment.id
            if is_service_job and not vals.get("southern_labor_product_id"):
                labor_product = self._southern_default_labor_product()
                if labor_product:
                    vals["southern_labor_product_id"] = labor_product.id
            if case:
                vals.setdefault("partner_id", case.partner_id.id)
                vals.setdefault(
                    "southern_client_equipment_id", case.client_equipment_id.id
                )
                vals.setdefault("southern_sale_order_id", case.sale_order_id.id)
                for task_field, case_field in self._SOUTHERN_SERVICE_NOTE_MAP.items():
                    vals.setdefault(task_field, case[case_field])
            if equipment:
                vals.setdefault("dmc_equipment", equipment.name)
                vals.setdefault(
                    "dmc_serial_number",
                    equipment.serial_no or "Unserialized",
                )
        skip_stage_email = all(
            vals.get(
                "southern_quote_workflow",
                self.env.context.get("default_southern_quote_workflow"),
            )
            for vals in vals_list
        )
        creator = (
            self.with_context(southern_skip_stage_email=True)
            if skip_stage_email
            else self
        )
        tasks = super(ProjectTask, creator).create(vals_list)
        if not self.env.context.get("southern_skip_auto_quote"):
            tasks._southern_auto_prepare_quote()
        return tasks

    def write(self, vals):
        result = super().write(vals)
        note_fields = self._SOUTHERN_SERVICE_NOTE_MAP.keys() & vals.keys()
        if note_fields:
            for task in self.filtered("southern_service_case_id"):
                task.southern_service_case_id.write(
                    {
                        self._SOUTHERN_SERVICE_NOTE_MAP[field_name]: vals[field_name]
                        for field_name in note_fields
                    }
                )
        if not self.env.context.get("southern_skip_auto_quote"):
            quote_fields = {
                "partner_id",
                "dmc_equipment",
                "dmc_serial_number",
                "southern_quote_workflow",
                "southern_labor_product_id",
                "southern_labor_rate",
            }
            if quote_fields.intersection(vals):
                self._southern_auto_prepare_quote()
        return result

    def _track_template(self, changes):
        templates = super()._track_template(changes)
        if self.env.context.get("southern_skip_stage_email"):
            templates.pop("stage_id", None)
        return templates
