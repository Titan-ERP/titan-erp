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
    southern_service_photo_ids = fields.One2many(
        "southern.service.photo",
        "client_equipment_id",
        string="Service Photos",
    )
    southern_service_case_count = fields.Integer(compute="_compute_southern_counts")
    southern_task_count = fields.Integer(compute="_compute_southern_counts")
    southern_repair_count = fields.Integer(compute="_compute_southern_counts")
    southern_service_photo_count = fields.Integer(
        compute="_compute_southern_counts"
    )

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
            case_photos = case.task_ids.mapped(
                "southern_service_photo_ids"
            ).filtered("include_in_ai")
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
                    "photo_evidence": [
                        {
                            "category": photo.category,
                            "caption": photo.caption,
                            "taken_at": fields.Datetime.to_string(
                                photo.captured_at
                            ),
                        }
                        for photo in case_photos[:6]
                    ],
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
                    "photo_evidence": [
                        {
                            "category": photo.category,
                            "caption": photo.caption,
                            "taken_at": fields.Datetime.to_string(
                                photo.captured_at
                            ),
                        }
                        for photo in task.southern_service_photo_ids.filtered(
                            "include_in_ai"
                        )[:6]
                    ],
                }
            )
        history.sort(key=lambda row: row["date"] or "", reverse=True)
        return history[:limit]

    def _southern_ai_commercial_history(self, current_task=None, limit=20):
        """Return completed service sales and posted invoice facts for AI context."""
        self.ensure_one()
        current_order = current_task.southern_sale_order_id if current_task else False
        orders = self.env["sale.order"].sudo().search(
            [
                ("southern_client_equipment_id", "=", self.id),
                ("southern_quote_type", "=", "service"),
                ("state", "in", ("sale", "done")),
            ],
            order="date_order desc, id desc",
        )
        if current_order:
            orders = orders.filtered(lambda order: order != current_order)

        history = []
        product_usage = {}
        total_actual_hours = 0.0
        posted_invoice_count = 0
        for order in orders:
            order_lines = order.order_line.filtered(
                lambda line: not line.display_type and line.product_id
            )
            tasks = self.sudo().southern_task_ids.filtered(
                lambda task: (
                    task.southern_sale_order_id == order
                    or task.sale_order_id == order
                )
            )
            actual_hours = sum(tasks.mapped("timesheet_ids.unit_amount"))
            total_actual_hours += actual_hours
            invoices = order.invoice_ids.filtered(
                lambda invoice: (
                    invoice.state == "posted"
                    and invoice.move_type in ("out_invoice", "out_refund")
                )
            )
            posted_invoice_count += len(invoices)
            for line in order_lines:
                usage = product_usage.setdefault(
                    line.product_id.id,
                    {
                        "product_code": line.product_id.default_code or "",
                        "product": line.product_id.display_name,
                        "ordered_quantity": 0.0,
                        "invoiced_quantity": 0.0,
                        "unit": line.product_uom_id.display_name or "",
                    },
                )
                usage["ordered_quantity"] += line.product_uom_qty
            for invoice in invoices:
                invoice_sign = -1.0 if invoice.move_type == "out_refund" else 1.0
                for invoice_line in invoice.invoice_line_ids.filtered(
                    lambda line: (
                        not line.display_type
                        and line.product_id
                        and order in line.sale_line_ids.order_id
                    )
                ):
                    usage = product_usage.setdefault(
                        invoice_line.product_id.id,
                        {
                            "product_code": (
                                invoice_line.product_id.default_code or ""
                            ),
                            "product": invoice_line.product_id.display_name,
                            "ordered_quantity": 0.0,
                            "invoiced_quantity": 0.0,
                            "unit": (
                                invoice_line.product_uom_id.display_name or ""
                            ),
                        },
                    )
                    usage["invoiced_quantity"] += (
                        invoice_sign * invoice_line.quantity
                    )
            history.append(
                {
                    "sales_order": order.name,
                    "date": fields.Datetime.to_string(order.date_order),
                    "commercial_basis": order.southern_commercial_basis or "",
                    "estimated_hours": order.southern_estimated_hours,
                    "actual_hours": actual_hours,
                    "products": [
                        {
                            "product_code": line.product_id.default_code or "",
                            "product": line.product_id.display_name,
                            "quantity": line.product_uom_qty,
                            "unit": line.product_uom_id.display_name or "",
                        }
                        for line in order_lines[:30]
                    ],
                    "posted_invoices": [
                        {
                            "date": fields.Date.to_string(invoice.invoice_date),
                            "type": invoice.move_type,
                            "payment_state": invoice.payment_state,
                            "products": [
                                {
                                    "product_code": (
                                        invoice_line.product_id.default_code or ""
                                    ),
                                    "product": invoice_line.product_id.display_name,
                                    "quantity": invoice_line.quantity,
                                    "unit": (
                                        invoice_line.product_uom_id.display_name or ""
                                    ),
                                }
                                for invoice_line in invoice.invoice_line_ids.filtered(
                                    lambda line: (
                                        not line.display_type
                                        and line.product_id
                                        and order in line.sale_line_ids.order_id
                                    )
                                )[:30]
                            ],
                        }
                        for invoice in invoices[:10]
                    ],
                }
            )
        return {
            "lifetime_summary": {
                "completed_service_order_count": len(orders),
                "posted_service_invoice_count": posted_invoice_count,
                "total_estimated_hours": sum(
                    orders.mapped("southern_estimated_hours")
                ),
                "total_actual_hours": total_actual_hours,
                "product_usage": list(product_usage.values()),
            },
            "recent_orders": history[:limit],
        }

    @api.depends(
        "southern_service_case_ids",
        "southern_task_ids",
        "southern_repair_order_ids",
        "southern_service_photo_ids",
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
            equipment.southern_service_photo_count = len(
                equipment.southern_service_photo_ids
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

    def action_view_southern_service_photos(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Service Photos"),
            "res_model": "southern.service.photo",
            "view_mode": "list,form",
            "domain": [("client_equipment_id", "=", self.id)],
        }
