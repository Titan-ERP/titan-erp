from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SouthernServiceWorkItem(models.Model):
    _name = "southern.service.work.item"
    _description = "Service Task"
    _order = "sequence, id"

    sequence = fields.Integer(default=10)
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
        index=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="task_id.southern_work_currency_id",
        readonly=True,
    )
    name = fields.Char(string="Task / Scope", required=True)
    work_type = fields.Selection(
        [
            ("labor", "Labor"),
            ("part", "Part"),
            ("travel", "Travel"),
            ("subcontract", "Subcontract"),
            ("other", "Other"),
        ],
        string="Type",
        required=True,
        default="labor",
    )
    assigned_user_id = fields.Many2one(
        "res.users",
        string="Technician",
        domain=[("share", "=", False), ("active", "=", True)],
    )
    allocated_hours = fields.Float(
        string="Allocated Hours",
        default=1.0,
    )
    quantity = fields.Float(
        string="Units",
        default=1.0,
    )
    quote_quantity = fields.Float(
        string="Quote Qty",
        compute="_compute_quote_values",
        store=True,
    )
    product_id = fields.Many2one(
        "product.product",
        string="Quote Product",
        domain=[("sale_ok", "=", True)],
    )
    product_uom_id = fields.Many2one(
        related="product_id.uom_id",
        string="Unit",
        readonly=True,
    )
    unit_price = fields.Monetary(
        string="Rate",
        currency_field="currency_id",
    )
    billable = fields.Boolean(default=True)
    subtotal = fields.Monetary(
        string="Quoted Amount",
        currency_field="currency_id",
        compute="_compute_quote_values",
        store=True,
    )
    state = fields.Selection(
        [
            ("planned", "Planned"),
            ("in_progress", "In Progress"),
            ("done", "Done"),
        ],
        required=True,
        default="planned",
    )
    sale_line_id = fields.Many2one(
        "sale.order.line",
        string="Quotation Line",
        readonly=True,
        copy=False,
        ondelete="set null",
    )

    @api.depends(
        "work_type",
        "allocated_hours",
        "quantity",
        "unit_price",
        "billable",
        "task_id.southern_labor_rate",
    )
    def _compute_quote_values(self):
        for item in self:
            item.quote_quantity = (
                item.allocated_hours
                if item.work_type == "labor"
                else item.quantity
            )
            rate = (
                item.task_id.southern_labor_rate
                if item.work_type == "labor"
                else item.unit_price
            )
            item.subtotal = item.quote_quantity * rate if item.billable else 0.0

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for item in self:
            if not item.product_id:
                continue
            if not item.name:
                item.name = item.product_id.display_name
            item.unit_price = item.product_id.lst_price

    @api.onchange("task_id")
    def _onchange_task_id(self):
        for item in self:
            if item.task_id and not item.assigned_user_id:
                item.assigned_user_id = item.task_id.user_ids[:1]

    @api.constrains(
        "billable",
        "product_id",
        "allocated_hours",
        "quantity",
        "work_type",
    )
    def _check_quote_inputs(self):
        for item in self:
            if (
                item.billable
                and item.work_type != "labor"
                and not item.product_id
            ):
                raise ValidationError(
                    _(
                        "A billable non-labor Service Task requires a "
                        "Quote Product."
                    )
                )
            if item.allocated_hours < 0 or item.quantity < 0:
                raise ValidationError(
                    _("Allocated Hours and Units cannot be negative.")
                )
            if item.billable and item.quote_quantity <= 0:
                raise ValidationError(
                    _("A billable Service Task requires a positive quote quantity.")
                )

    def _prepare_sale_line_values(self, order):
        self.ensure_one()
        return {
            "order_id": order.id,
            "sequence": self.sequence,
            "product_id": self.product_id.id,
            "name": self.name,
            "product_uom_qty": self.quote_quantity,
            "product_uom_id": self.product_uom_id.id,
            "price_unit": self.unit_price,
        }

    def _sync_individual_to_quotation(self, order):
        for item in self:
            line = item.sale_line_id
            if not item.billable:
                if line:
                    if line.order_id.state not in ("draft", "sent"):
                        raise ValidationError(
                            _(
                                "A confirmed quotation line cannot be removed "
                                "from its Service Task."
                            )
                        )
                    item.write({"sale_line_id": False})
                    line.unlink()
                continue
            if order.state not in ("draft", "sent"):
                raise ValidationError(
                    _("Confirmed Sales Orders cannot be changed from Service Tasks.")
                )
            values = item._prepare_sale_line_values(order)
            if line and line.order_id == order:
                values.pop("order_id")
                line.write(values)
            else:
                if line and line.order_id.state in ("draft", "sent"):
                    line.unlink()
                line = self.env["sale.order.line"].create(values)
                item.write({"sale_line_id": line.id})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        Product = self.env["product.product"]
        Task = self.env["project.task"]
        for values in vals_list:
            if values.get("work_type", "labor") != "labor":
                values.setdefault("allocated_hours", 0.0)
            product = (
                Product.browse(values["product_id"])
                if values.get("product_id")
                else Product
            )
            task = (
                Task.browse(values["task_id"])
                if values.get("task_id")
                else Task
            )
            if product:
                values.setdefault("name", product.display_name)
                values.setdefault("unit_price", product.lst_price)
            if task and not values.get("assigned_user_id"):
                values["assigned_user_id"] = task.user_ids[:1].id
        items = super().create(vals_list)
        items.mapped("task_id")._southern_sync_tasks_to_quotation()
        return items

    def write(self, vals):
        quote_fields = {
            "sequence",
            "name",
            "work_type",
            "allocated_hours",
            "quantity",
            "product_id",
            "unit_price",
            "billable",
        }
        result = super().write(vals)
        if (
            not self.env.context.get("southern_skip_quote_sync")
            and quote_fields.intersection(vals)
        ):
            self.mapped("task_id")._southern_sync_tasks_to_quotation()
        return result

    def unlink(self):
        tasks = self.mapped("task_id")
        for item in self:
            line = item.sale_line_id
            if line and line.order_id.state not in ("draft", "sent"):
                raise ValidationError(
                    _("A Service Task linked to a confirmed Sales Order cannot be deleted.")
                )
        lines = self.filtered(
            lambda item: item.work_type != "labor"
        ).sale_line_id.filtered(
            lambda line: line.order_id.state in ("draft", "sent")
        )
        result = super().unlink()
        lines.unlink()
        tasks._southern_sync_tasks_to_quotation()
        return result
