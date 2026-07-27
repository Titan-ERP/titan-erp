from odoo import api, fields, models


class SouthernPartsEvidenceQueue(models.Model):
    _name = "southern.parts.evidence.queue"
    _description = "Southern Parts Evidence Queue"
    _order = "priority desc, next_check_at, id"

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    priority = fields.Selection(
        [
            ("0", "Low"),
            ("1", "Normal"),
            ("2", "High"),
            ("3", "Urgent"),
        ],
        default="1",
        required=True,
        index=True,
    )
    status = fields.Selection(
        [
            ("queued", "Queued"),
            ("exact_evidence_found", "Exact Evidence Found"),
            ("currency_review", "Currency Review"),
            ("alternate_source_needed", "Alternate Source Needed"),
            ("rate_limited", "Rate Limited"),
            ("ready_for_products_agent_review", "Ready for Products Agent Review"),
            ("applied", "Applied"),
            ("blocked", "Blocked"),
            ("rejected", "Rejected"),
        ],
        default="queued",
        required=True,
        index=True,
    )
    evidence_type = fields.Selection(
        [
            ("pricing", "Pricing"),
            ("image", "Image"),
            ("parts_intelligence", "Parts Intelligence"),
            ("taxonomy", "Taxonomy"),
            ("publication", "Publication Readiness"),
        ],
        default="pricing",
        required=True,
        index=True,
    )
    product_tmpl_id = fields.Many2one("product.template", string="Product", ondelete="set null", index=True)
    default_code = fields.Char(string="Internal Reference", index=True)
    source_name = fields.Char(index=True)
    source_url = fields.Char(string="Evidence URL")
    source_search_url = fields.Char(string="Search URL")
    source_title = fields.Char(string="Source Title")
    observed_price = fields.Float(string="Observed Price")
    currency_code = fields.Char(string="Currency", index=True)
    confidence = fields.Float(default=0.0)
    blocker_reason = fields.Char(index=True)
    notes = fields.Text()
    external_key = fields.Char(index=True)
    retry_count = fields.Integer(default=0)
    last_checked_at = fields.Datetime()
    next_check_at = fields.Datetime(index=True)
    reviewed_at = fields.Datetime()
    applied_at = fields.Datetime()
    reviewed_by_id = fields.Many2one("res.users", readonly=True)

    @api.depends("default_code", "evidence_type", "source_name", "status")
    def _compute_name(self):
        for queue in self:
            parts = [
                queue.default_code or "No SKU",
                dict(queue._fields["evidence_type"].selection).get(queue.evidence_type, queue.evidence_type),
                queue.source_name or "No Source",
                dict(queue._fields["status"].selection).get(queue.status, queue.status),
            ]
            queue.name = " / ".join(parts)

    @api.onchange("product_tmpl_id")
    def _onchange_product_tmpl_id(self):
        for queue in self:
            if queue.product_tmpl_id and not queue.default_code:
                queue.default_code = queue.product_tmpl_id.default_code

    @api.model_create_multi
    def create(self, vals_list):
        Product = self.env["product.template"].sudo()
        for vals in vals_list:
            if vals.get("default_code") and not vals.get("product_tmpl_id"):
                product = Product.search([("default_code", "=", vals["default_code"])], limit=1)
                if product:
                    vals["product_tmpl_id"] = product.id
            if vals.get("product_tmpl_id") and not vals.get("default_code"):
                product = Product.browse(vals["product_tmpl_id"])
                vals["default_code"] = product.default_code
            if not vals.get("external_key"):
                vals["external_key"] = self._build_external_key(vals)
        return super().create(vals_list)

    @api.model
    def _build_external_key(self, vals):
        parts = [
            vals.get("default_code") or "",
            vals.get("evidence_type") or "",
            vals.get("source_name") or "",
            vals.get("source_url") or "",
        ]
        return "|".join(str(part).strip().lower() for part in parts)

    def action_mark_ready_for_review(self):
        self.write(
            {
                "status": "ready_for_products_agent_review",
                "reviewed_at": fields.Datetime.now(),
                "reviewed_by_id": self.env.user.id,
            }
        )
        return True

    def action_mark_currency_review(self):
        self.write({"status": "currency_review"})
        return True

    def action_mark_alternate_source_needed(self):
        self.write({"status": "alternate_source_needed"})
        return True

    def action_mark_rate_limited(self):
        for queue in self:
            queue.write({"status": "rate_limited", "retry_count": queue.retry_count + 1})
        return True

    def action_mark_applied(self):
        self.write({"status": "applied", "applied_at": fields.Datetime.now()})
        return True

    def action_requeue(self):
        self.write({"status": "queued", "blocker_reason": False})
        return True

    _external_key_unique = models.Constraint(
        "unique(external_key)",
        "Evidence queue item already exists for this SKU, type, source, and URL.",
    )
