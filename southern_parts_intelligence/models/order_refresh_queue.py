from odoo import api, fields, models


class SouthernPartsOrderRefreshQueue(models.Model):
    _name = "southern.parts.order.refresh.queue"
    _description = "Southern Parts Order Refresh Queue"
    _order = "priority desc, create_date, id"

    product_tmpl_id = fields.Many2one("product.template", required=True, index=True, ondelete="cascade")
    default_code = fields.Char(related="product_tmpl_id.default_code", store=True, readonly=True, index=True)
    source_url = fields.Char(related="product_tmpl_id.southern_source_url", store=True, readonly=True)
    trigger_model = fields.Char(required=True, index=True)
    trigger_res_id = fields.Integer(required=True, index=True)
    trigger_name = fields.Char()
    trigger_kind = fields.Selection(
        [
            ("sale_order", "Sales Order"),
            ("purchase_order", "Purchase Order"),
            ("manual", "Manual"),
        ],
        required=True,
        index=True,
    )
    refresh_cost = fields.Boolean(default=True)
    refresh_retail = fields.Boolean(default=True)
    refresh_source = fields.Boolean(default=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("running", "Running"),
            ("done", "Done"),
            ("error", "Error"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    priority = fields.Integer(default=10)
    attempt_count = fields.Integer(default=0)
    last_attempt_at = fields.Datetime()
    last_done_at = fields.Datetime()
    last_message = fields.Text()

    @api.model
    def enqueue_products(self, products, trigger_record, trigger_kind, refresh_cost=True, refresh_retail=True, refresh_source=True):
        products = products.filtered(lambda product: product.default_code and product.default_code.startswith("S."))
        queued = self.browse()
        for product in products:
            existing = self.search(
                [
                    ("product_tmpl_id", "=", product.id),
                    ("state", "in", ["pending", "running"]),
                ],
                limit=1,
            )
            values = {
                "trigger_model": trigger_record._name,
                "trigger_res_id": trigger_record.id,
                "trigger_name": getattr(trigger_record, "name", "") or "",
                "trigger_kind": trigger_kind,
                "refresh_cost": refresh_cost,
                "refresh_retail": refresh_retail,
                "refresh_source": refresh_source,
                "priority": 50 if trigger_kind == "sale_order" else 40,
                "last_message": "Queued from %s %s." % (trigger_record._name, getattr(trigger_record, "name", trigger_record.id)),
            }
            if existing:
                existing.write(values)
                queued |= existing
            else:
                queued |= self.create(dict(values, product_tmpl_id=product.id))
        return queued

    def action_reset_to_pending(self):
        self.write({"state": "pending", "last_message": "Reset to pending."})
