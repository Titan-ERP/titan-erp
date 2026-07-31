import json
import logging
import os
import urllib.error
import urllib.request

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


_logger = logging.getLogger(__name__)


class SouthernServiceAiSuggestion(models.Model):
    _name = "southern.service.ai.suggestion"
    _description = "AI Service Estimate Suggestion"
    _order = "create_date desc, id desc"
    _rec_name = "name"

    name = fields.Char(
        string="Review",
        compute="_compute_name",
        store=True,
    )

    task_id = fields.Many2one(
        "project.task",
        string="Service Job",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="task_id.company_id",
        store=True,
        readonly=True,
    )
    state = fields.Selection(
        [
            ("draft", "Needs Review"),
            ("applied", "Applied"),
            ("rejected", "Rejected"),
        ],
        default="draft",
        required=True,
        index=True,
    )
    model_name = fields.Char(readonly=True)
    response_id = fields.Char(string="OpenAI Response", readonly=True, copy=False)
    summary = fields.Text(readonly=True)
    customer_note = fields.Text(
        string="Proposed Customer Quotation Note",
        help="Editable draft. It is not added to the quotation until Apply Selected is used.",
    )
    assumptions = fields.Text(readonly=True)
    questions = fields.Text(string="Questions for Technician", readonly=True)
    confidence = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High")],
        readonly=True,
    )
    line_ids = fields.One2many(
        "southern.service.ai.suggestion.line",
        "suggestion_id",
        string="Suggested Work",
        copy=False,
    )

    @api.depends("task_id.name")
    def _compute_name(self):
        for suggestion in self:
            suggestion.name = _("AI Estimate Review - %(job)s") % {
                "job": suggestion.task_id.name or _("Service Job")
            }

    def action_apply_selected(self):
        for suggestion in self:
            if suggestion.state != "draft":
                raise ValidationError(_("Only draft AI suggestions can be applied."))
            task = suggestion.task_id
            order = task._southern_get_or_create_sale_order()
            if order.state not in ("draft", "sent"):
                raise ValidationError(
                    _("AI suggestions cannot change a confirmed Sales Order.")
                )

            selected = suggestion.line_ids.filtered("selected")
            if selected.filtered(lambda line: line.work_type == "labor") and not (
                task.southern_labor_product_id
            ):
                raise ValidationError(
                    _(
                        "Select the Labor Sales Product on the Service Job before "
                        "applying suggested labor tasks."
                    )
                )
            missing_products = selected.filtered(
                lambda line: line.work_type == "part" and not line.product_id
            )
            if missing_products:
                raise ValidationError(
                    _(
                        "Choose a real Odoo Product for every selected part "
                        "before applying the estimate."
                    )
                )

            WorkItem = self.env["southern.service.work.item"]
            for line in selected:
                values = {
                    "task_id": task.id,
                    "name": line.name,
                    "work_type": line.work_type,
                    "billable": True,
                }
                if line.work_type == "labor":
                    values.update(
                        {
                            "allocated_hours": line.estimated_hours,
                            "assigned_user_id": task.user_ids[:1].id,
                        }
                    )
                else:
                    values.update(
                        {
                            "product_id": line.product_id.id,
                            "quantity": line.quantity,
                            "unit_price": line.product_id.lst_price,
                        }
                    )
                WorkItem.create(values)

            if suggestion.customer_note:
                note_line = task.southern_ai_note_line_id
                note_values = {
                    "order_id": order.id,
                    "southern_service_task_id": task.id,
                    "display_type": "line_note",
                    "name": suggestion.customer_note,
                }
                if note_line and note_line.order_id == order:
                    note_values.pop("order_id")
                    note_line.write(note_values)
                else:
                    note_line = self.env["sale.order.line"].create(note_values)
                    task.with_context(southern_skip_auto_quote=True).write(
                        {"southern_ai_note_line_id": note_line.id}
                    )

            task._southern_sync_tasks_to_quotation(order)
            suggestion.state = "applied"
            task.message_post(
                body=_(
                    "AI estimate reviewed and applied: %(count)s selected item(s).",
                    count=len(selected),
                )
            )
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_reject(self):
        self.filtered(lambda suggestion: suggestion.state == "draft").write(
            {"state": "rejected"}
        )
        return {"type": "ir.actions.client", "tag": "reload"}


class SouthernServiceAiSuggestionLine(models.Model):
    _name = "southern.service.ai.suggestion.line"
    _description = "AI Service Estimate Suggestion Line"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
    suggestion_id = fields.Many2one(
        "southern.service.ai.suggestion",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="suggestion_id.company_id",
        store=True,
        readonly=True,
    )
    selected = fields.Boolean(default=True)
    work_type = fields.Selection(
        [("labor", "Labor Task"), ("part", "Part")],
        required=True,
    )
    name = fields.Char(string="Suggested Task / Part", required=True)
    estimated_hours = fields.Float(string="Likely Hours")
    hours_low = fields.Float(string="Low Hours")
    hours_high = fields.Float(string="High Hours")
    quantity = fields.Float(default=1.0)
    product_id = fields.Many2one(
        "product.product",
        string="Matched Odoo Product",
        domain=[("sale_ok", "=", True)],
    )
    requested_product_code = fields.Char(string="Suggested Product Code", readonly=True)
    reason = fields.Text(readonly=True)
    manual_reference = fields.Char(string="Manual / Bulletin Reference", readonly=True)
    confidence = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High")],
        readonly=True,
    )

    @api.constrains("estimated_hours", "hours_low", "hours_high", "quantity")
    def _check_positive_values(self):
        for line in self:
            if min(
                line.estimated_hours,
                line.hours_low,
                line.hours_high,
                line.quantity,
            ) < 0:
                raise ValidationError(_("AI estimate quantities cannot be negative."))


class ProjectTaskAiEstimate(models.Model):
    _inherit = "project.task"

    southern_ai_suggestion_ids = fields.One2many(
        "southern.service.ai.suggestion",
        "task_id",
        string="AI Estimate Reviews",
        copy=False,
    )
    southern_ai_current_suggestion_id = fields.Many2one(
        "southern.service.ai.suggestion",
        string="Current AI Estimate",
        compute="_compute_southern_ai_current_suggestion",
    )
    southern_ai_note_line_id = fields.Many2one(
        "sale.order.line",
        string="AI Quotation Note",
        readonly=True,
        copy=False,
        ondelete="set null",
    )

    @api.depends("southern_ai_suggestion_ids", "southern_ai_suggestion_ids.state")
    def _compute_southern_ai_current_suggestion(self):
        for task in self:
            task.southern_ai_current_suggestion_id = task.southern_ai_suggestion_ids[:1]

    def _southern_openai_settings(self):
        parameters = self.env["ir.config_parameter"].sudo()
        api_key = os.environ.get("OPENAI_API_KEY") or parameters.get_param(
            "southern_service_operations.openai_api_key"
        )
        if not api_key:
            raise UserError(
                _(
                    "OpenAI is not configured. Ask an Odoo administrator to "
                    "enter the API key under Settings > Southern Service AI."
                )
            )
        return {
            "api_key": api_key,
            "model": parameters.get_param(
                "southern_service_operations.openai_model", "gpt-5.6-sol"
            ),
            "vector_store_id": parameters.get_param(
                "southern_service_operations.openai_vector_store_id"
            ),
        }

    def _southern_product_catalog(self):
        products = self.env["product.product"].search(
            [
                ("sale_ok", "=", True),
                ("active", "=", True),
                ("type", "=", "consu"),
            ],
            limit=250,
            order="default_code, name, id",
        )
        return [
            {
                "code": product.default_code or "",
                "name": product.display_name,
                "price": product.lst_price,
            }
            for product in products
        ]

    def _southern_ai_input(self):
        self.ensure_one()
        equipment = self.southern_client_equipment_id
        inspection_items = self.southern_inspection_item_ids.filtered(
            "include_in_ai"
        )
        service_photos = self.southern_service_photo_ids.filtered("include_in_ai")
        return {
            "customer_complaint": self.description or self.name or "",
            "equipment": self.dmc_equipment or "",
            "serial_number": self.dmc_serial_number or "",
            "run_hours": self.dmc_equipment_run_hours or 0.0,
            "equipment_record": {
                "manufacturer": equipment.manufacturer_id.display_name or "",
                "model": equipment.model or "",
                "category": equipment.category_id.display_name or "",
                "asset_tag": equipment.asset_tag or "",
                "systems": equipment.system_ids.mapped("display_name"),
                "service_history": (
                    equipment._southern_ai_service_history(
                        current_task=self,
                        limit=12,
                    )
                    if equipment
                    else []
                ),
            },
            "digital_equipment_inspection": {
                "status": self.southern_inspection_state,
                "summary": self.southern_inspection_summary or "",
                "items": [
                    {
                        "area": item.inspection_area,
                        "item": item.name,
                        "result": item.result,
                        "priority": item.priority,
                        "measurement": item.measurement or "",
                        "fault_code": item.fault_code or "",
                        "finding": item.finding or "",
                        "recommended_action": item.recommended_action or "",
                    }
                    for item in inspection_items
                ],
                "photo_evidence": [
                    {
                        "category": photo.category,
                        "caption": photo.caption,
                        "taken_at": fields.Datetime.to_string(photo.captured_at),
                    }
                    for photo in service_photos
                ],
            },
            "technician_diagnosis": self.southern_diagnosis or "",
            "work_performed": self.southern_work_performed or "",
            "recommendations": self.southern_recommendations or "",
            "available_parts_catalog": self._southern_product_catalog(),
        }

    @api.model
    def _southern_ai_schema(self):
        confidence = {"type": "string", "enum": ["low", "medium", "high"]}
        line_properties = {
            "work_type": {"type": "string", "enum": ["labor", "part"]},
            "name": {"type": "string"},
            "estimated_hours": {"type": "number", "minimum": 0},
            "hours_low": {"type": "number", "minimum": 0},
            "hours_high": {"type": "number", "minimum": 0},
            "quantity": {"type": "number", "minimum": 0},
            "product_code": {"type": "string"},
            "reason": {"type": "string"},
            "manual_reference": {"type": "string"},
            "confidence": confidence,
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "summary": {"type": "string"},
                "customer_note": {"type": "string"},
                "assumptions": {"type": "array", "items": {"type": "string"}},
                "questions": {"type": "array", "items": {"type": "string"}},
                "confidence": confidence,
                "lines": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "properties": line_properties,
                        "required": list(line_properties),
                    },
                },
            },
            "required": [
                "summary",
                "customer_note",
                "assumptions",
                "questions",
                "confidence",
                "lines",
            ],
        }

    def _southern_call_openai(self, settings, service_input):
        payload = {
            "model": settings["model"],
            "store": False,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You assist heavy-equipment service technicians. Produce a "
                        "conservative diagnostic work plan and draft estimate. Treat "
                        "customer statements as symptoms, not confirmed causes. Never "
                        "invent a completed diagnosis, part number, warranty decision, "
                        "price, or safety assurance. Recommend parts only when their "
                        "exact product code appears in available_parts_catalog; otherwise "
                        "leave product_code empty. Cite an equipment manual or bulletin "
                        "only when file search supplies it. Treat Digital Equipment "
                        "Inspection entries as technician-recorded evidence, preserve "
                        "their inspection status, and do not convert a Monitor item into "
                        "a required repair without a stated technical reason. Make the customer_note clear "
                        "that proposed work is subject to technician inspection and approval."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(service_input, default=str),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "service_estimate_suggestion",
                    "strict": True,
                    "schema": self._southern_ai_schema(),
                }
            },
        }
        if settings["vector_store_id"]:
            payload["tools"] = [
                {
                    "type": "file_search",
                    "vector_store_ids": [settings["vector_store_id"]],
                    "max_num_results": 6,
                }
            ]
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {settings['api_key']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            safe_detail = _("OpenAI returned HTTP %(status)s.", status=error.code)
            _logger.warning("Southern Service AI request failed: HTTP %s", error.code)
            raise UserError(safe_detail) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            _logger.warning("Southern Service AI request failed: %s", type(error).__name__)
            raise UserError(
                _("OpenAI could not be reached. Try again or contact an administrator.")
            ) from error

    @api.model
    def _southern_extract_openai_text(self, response):
        if response.get("output_text"):
            return response["output_text"]
        for output in response.get("output", []):
            if output.get("type") != "message":
                continue
            for content in output.get("content", []):
                if content.get("type") == "output_text" and content.get("text"):
                    return content["text"]
        raise UserError(_("OpenAI returned no usable estimate."))

    def action_southern_generate_ai_estimate(self):
        self.ensure_one()
        if not self.is_fsm:
            raise ValidationError(_("AI estimates are available only for Service Jobs."))
        if not (self.description or self.name):
            raise ValidationError(
                _("Record the customer complaint or requested work first.")
            )
        settings = self._southern_openai_settings()
        response = self._southern_call_openai(settings, self._southern_ai_input())
        try:
            result = json.loads(self._southern_extract_openai_text(response))
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise UserError(_("OpenAI returned an invalid estimate format.")) from error

        Product = self.env["product.product"]
        line_commands = []
        for index, line in enumerate(result.get("lines", []), start=1):
            product = Product
            code = line.get("product_code", "").strip()
            if code:
                product = Product.search(
                    [("default_code", "=", code), ("sale_ok", "=", True)],
                    limit=1,
                )
            line_commands.append(
                fields.Command.create(
                    {
                        "sequence": index * 10,
                        "selected": line.get("confidence") != "low",
                        "work_type": line["work_type"],
                        "name": line["name"],
                        "estimated_hours": line["estimated_hours"],
                        "hours_low": line["hours_low"],
                        "hours_high": line["hours_high"],
                        "quantity": line["quantity"],
                        "product_id": product.id,
                        "requested_product_code": code,
                        "reason": line["reason"],
                        "manual_reference": line["manual_reference"],
                        "confidence": line["confidence"],
                    }
                )
            )
        suggestion = self.env["southern.service.ai.suggestion"].create(
            {
                "task_id": self.id,
                "model_name": settings["model"],
                "response_id": response.get("id"),
                "summary": result["summary"],
                "customer_note": result["customer_note"],
                "assumptions": "\n".join(f"- {item}" for item in result["assumptions"]),
                "questions": "\n".join(f"- {item}" for item in result["questions"]),
                "confidence": result["confidence"],
                "line_ids": line_commands,
            }
        )
        self.message_post(
            body=_(
                "AI estimate generated for technician review using %(model)s.",
                model=settings["model"],
            )
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Review AI Estimate"),
            "res_model": "southern.service.ai.suggestion",
            "view_mode": "form",
            "res_id": suggestion.id,
            "target": "new",
        }
