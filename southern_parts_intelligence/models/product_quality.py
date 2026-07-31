from collections import Counter

from odoo import _, api, fields, models


ISSUE_TYPES = [
    ("placeholder_price", "Placeholder Price"),
    ("price_not_above_cost", "Price Not Above Cost"),
    ("missing_evidence", "Missing Evidence"),
    ("taxonomy_review", "Taxonomy Review"),
    ("duplicate_reference", "Duplicate Internal Reference"),
    ("published_missing_image", "Published Without Image"),
    ("published_missing_description", "Published Without Description"),
    ("publication_ready", "Publication Ready"),
]


class SouthernProductQualityIssue(models.Model):
    _name = "southern.product.quality.issue"
    _description = "Southern Product Master Quality Issue"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "severity desc, detected_at desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    product_tmpl_id = fields.Many2one(
        "product.template",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    issue_type = fields.Selection(ISSUE_TYPES, required=True, index=True, tracking=True)
    severity = fields.Selection(
        [("1_low", "Low"), ("2_medium", "Medium"), ("3_high", "High"), ("4_blocker", "Blocker")],
        default="2_medium",
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ("open", "Open"),
            ("in_progress", "In Progress"),
            ("blocked", "Blocked"),
            ("resolved", "Resolved"),
            ("dismissed", "Dismissed"),
        ],
        default="open",
        required=True,
        tracking=True,
        index=True,
    )
    assigned_to_id = fields.Many2one("res.users", tracking=True, index=True)
    detected_at = fields.Datetime(default=fields.Datetime.now, required=True)
    last_detected_at = fields.Datetime(default=fields.Datetime.now, required=True)
    resolved_at = fields.Datetime(readonly=True)
    details = fields.Text()
    resolution_note = fields.Text(tracking=True)
    product_published = fields.Boolean(related="product_tmpl_id.website_published", store=True)
    internal_reference = fields.Char(related="product_tmpl_id.default_code", store=True, index=True)

    @api.depends(
        "product_tmpl_id",
        "product_tmpl_id.name",
        "product_tmpl_id.default_code",
        "issue_type",
    )
    def _compute_name(self):
        labels = dict(ISSUE_TYPES)
        for issue in self:
            issue.name = "%s: %s" % (
                labels.get(issue.issue_type, _("Quality Issue")),
                issue.product_tmpl_id.display_name,
            )

    def action_start(self):
        self.write({"state": "in_progress"})

    def action_resolve(self):
        self.write({"state": "resolved", "resolved_at": fields.Datetime.now()})

    def action_reopen(self):
        self.write({"state": "open", "resolved_at": False})

    @api.model
    def _issue_codes(self, product, duplicate_counts):
        codes = []
        price = product.list_price or 0.0
        cost = product.standard_price or 0.0
        reference = "".join((product.default_code or "").upper().split())
        evidence_count = sum(
            getattr(product, field_name, 0) or 0
            for field_name in (
                "southern_specification_count",
                "southern_fitment_count",
                "southern_oem_reference_count",
                "southern_catalog_page_count",
            )
        )
        if price <= 1.49:
            codes.append("placeholder_price")
        elif cost > 0 and price <= cost:
            codes.append("price_not_above_cost")
        if not product.southern_source_url and not evidence_count:
            codes.append("missing_evidence")
        if product.website_published and not product.public_categ_ids:
            codes.append("taxonomy_review")
        if reference and duplicate_counts[reference] > 1:
            codes.append("duplicate_reference")
        if product.website_published and not product.image_128:
            codes.append("published_missing_image")
        if product.website_published and not (
            product.description_ecommerce or product.description_sale
        ):
            codes.append("published_missing_description")
        if (
            not product.website_published
            and price > max(cost, 1.49)
            and product.public_categ_ids
            and product.image_128
            and (product.southern_source_url or evidence_count)
            and (product.description_ecommerce or product.description_sale)
        ):
            codes.append("publication_ready")
        return codes

    @api.model
    def refresh_quality_queue(self, limit=None, after_id=0):
        Product = self.env["product.template"].with_context(
            active_test=False, bin_size=True
        )
        product_domain = [
            ("company_id", "in", [False, self.env.company.id]),
            ("id", ">", int(after_id or 0)),
        ]
        products = Product.search(
            product_domain,
            order="id",
            limit=limit,
        )
        raw_references = [
            reference
            for reference in products.mapped("default_code")
            if reference
        ]
        duplicate_candidates = Product.search(
            [
                ("company_id", "in", [False, self.env.company.id]),
                ("default_code", "in", raw_references),
            ]
        )
        duplicate_counts = Counter(
            "".join((reference or "").upper().split())
            for reference in duplicate_candidates.mapped("default_code")
            if reference
        )
        now = fields.Datetime.now()
        detected = set()
        created = updated = resolved = 0
        for product in products:
            for issue_type in self._issue_codes(product, duplicate_counts):
                key = (product.id, issue_type, self.env.company.id)
                detected.add(key)
                issue = self.search(
                    [
                        ("product_tmpl_id", "=", product.id),
                        ("issue_type", "=", issue_type),
                        ("company_id", "=", self.env.company.id),
                        ("state", "not in", ["resolved", "dismissed"]),
                    ],
                    limit=1,
                )
                values = {
                    "last_detected_at": now,
                }
                if issue:
                    issue.write(values)
                    updated += 1
                else:
                    self.create(
                        dict(
                            values,
                            product_tmpl_id=product.id,
                            company_id=self.env.company.id,
                            issue_type=issue_type,
                            severity=(
                                "4_blocker"
                                if issue_type in ("placeholder_price", "price_not_above_cost")
                                and product.website_published
                                else "3_high"
                                if issue_type.startswith("published_")
                                else "2_medium"
                            ),
                        )
                    )
                    created += 1
        product_ids = products.ids
        open_issues = self.search(
            [
                ("company_id", "=", self.env.company.id),
                ("product_tmpl_id", "in", product_ids),
                ("state", "in", ["open", "in_progress", "blocked"]),
            ]
        )
        for issue in open_issues:
            if (issue.product_tmpl_id.id, issue.issue_type, issue.company_id.id) not in detected:
                issue.write(
                    {
                        "state": "resolved",
                        "resolved_at": now,
                        "resolution_note": _("Automatically resolved by the quality refresh."),
                    }
                )
                resolved += 1
        return {
            "created": created,
            "updated": updated,
            "resolved": resolved,
            "scanned": len(products),
            "last_product_id": products[-1].id if products else 0,
        }

    @api.model
    def cron_refresh_quality_queue(self):
        self.env.cr.execute(
            "SELECT pg_try_advisory_xact_lock(hashtext(%s))",
            ("southern_product_quality_queue",),
        )
        if not self.env.cr.fetchone()[0]:
            return {"skipped": "already_running"}

        Config = self.env["ir.config_parameter"].sudo()
        results = {}
        for company in self.env["res.company"].sudo().search([]):
            key = f"southern_parts_intelligence.quality_cursor.{company.id}"
            cursor = int(Config.get_param(key, "0") or 0)
            queue = self.with_company(company)
            result = queue.refresh_quality_queue(limit=500, after_id=cursor)
            if not result["scanned"] and cursor:
                result = queue.refresh_quality_queue(limit=500, after_id=0)
            Config.set_param(key, str(result["last_product_id"]))
            results[company.id] = result
        return results

    @api.model
    def action_refresh_quality_queue(self):
        self.cron_refresh_quality_queue()
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }
