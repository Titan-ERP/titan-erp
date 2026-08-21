from collections import Counter, defaultdict
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..quality_rules import (
    ISSUE_TYPES,
    QUALITY_BATCH_LIMIT,
    QUALITY_PRIORITY_OPEN_LIMIT,
    QUALITY_PRIORITY_PUBLISHED_LIMIT,
    QUALITY_PRIORITY_UNSEEN_PUBLISHED_LIMIT,
    QUALITY_STALE_DAYS,
    WORK_LANES,
    classify_product_quality,
    dismissed_should_reopen,
    fact_key,
    finding_fact_key,
    merge_quality_refresh_ids,
    next_action_for,
    prioritize_published_refresh_ids,
    severity_for,
    work_lane_for,
)
from .catalog_agents import customer_description_ready, normalized_sparex_sku


class SouthernProductQualityIssue(models.Model):
    _name = "southern.product.quality.issue"
    _description = "Southern Product Master Quality Issue"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "severity desc, work_lane, last_detected_at desc, id desc"

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
    work_lane = fields.Selection(
        WORK_LANES,
        compute="_compute_lane_and_severity",
        store=True,
        index=True,
    )
    severity = fields.Selection(
        [("1_low", "Low"), ("2_medium", "Medium"), ("3_high", "High"), ("4_blocker", "Blocker")],
        compute="_compute_lane_and_severity",
        store=True,
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
    accepted_fact_key = fields.Char(readonly=True, copy=False)
    next_action = fields.Char(compute="_compute_next_action", store=True)
    resolution_note = fields.Text(tracking=True)
    product_published = fields.Boolean(related="product_tmpl_id.website_published", store=True)
    internal_reference = fields.Char(related="product_tmpl_id.default_code", store=True, index=True)
    sourcing_queue_id = fields.Many2one(
        "southern.sparex.sourcing.queue",
        compute="_compute_related_work",
    )
    discovery_item_id = fields.Many2one(
        "southern.sparex.discovery.item",
        compute="_compute_related_work",
    )
    evidence_queue_id = fields.Many2one(
        "southern.parts.evidence.queue",
        compute="_compute_related_work",
    )
    is_stale = fields.Boolean(compute="_compute_is_stale")

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

    @api.depends("issue_type", "product_published")
    def _compute_lane_and_severity(self):
        for issue in self:
            published = bool(issue.product_published)
            issue.work_lane = work_lane_for(issue.issue_type, published)
            issue.severity = severity_for(issue.issue_type, published)

    @api.depends("issue_type")
    def _compute_next_action(self):
        for issue in self:
            issue.next_action = next_action_for(issue.issue_type)

    @api.depends(
        "company_id",
        "product_tmpl_id",
        "product_tmpl_id.default_code",
        "product_tmpl_id.southern_sparex_sourcing_ids",
    )
    def _compute_related_work(self):
        Discovery = self.env["southern.sparex.discovery.item"].sudo()
        Evidence = self.env["southern.parts.evidence.queue"]
        for issue in self:
            product = issue.product_tmpl_id
            issue.sourcing_queue_id = product.southern_sparex_sourcing_ids.filtered(
                lambda row: row.company_id == issue.company_id
            )[:1]
            normalized = normalized_sparex_sku(product.default_code)
            discovery = Discovery.browse()
            if normalized:
                discovery = Discovery.search(
                    [
                        ("company_id", "=", issue.company_id.id),
                        ("normalized_sku", "=", normalized),
                    ],
                    limit=1,
                )
            issue.discovery_item_id = discovery
            issue.evidence_queue_id = Evidence.search(
                [
                    ("product_tmpl_id", "=", product.id),
                    ("status", "not in", ["applied", "rejected"]),
                ],
                order="priority desc, id desc",
                limit=1,
            )

    @api.depends("last_detected_at", "state")
    def _compute_is_stale(self):
        cutoff = fields.Datetime.now() - timedelta(days=QUALITY_STALE_DAYS)
        open_states = {"open", "in_progress", "blocked"}
        for issue in self:
            issue.is_stale = issue.state in open_states and (
                not issue.last_detected_at or issue.last_detected_at < cutoff
            )

    def action_start(self):
        self.write({"state": "in_progress"})

    def action_assign_to_me(self):
        self.write({"assigned_to_id": self.env.user.id, "state": "in_progress"})
        return True

    def action_resolve(self):
        still_present = self.browse()
        for company in self.mapped("company_id"):
            issues = self.filtered(lambda issue: issue.company_id == company)
            queue = self.with_company(company)
            duplicate_counts = queue._duplicate_counts_for(issues.mapped("product_tmpl_id"))
            for issue in issues:
                codes = {
                    finding.issue_type
                    for finding in queue._product_findings(issue.product_tmpl_id, duplicate_counts)
                }
                if issue.issue_type in codes:
                    still_present |= issue
        if still_present:
            raise UserError(
                _(
                    "These quality rows are still present: %(names)s. "
                    "Resolve only after the product facts change, or Dismiss with a "
                    "note if the exception is accepted."
                )
                % {"names": ", ".join(still_present.mapped("display_name"))}
            )
        self.write({"state": "resolved", "resolved_at": fields.Datetime.now()})
        return True

    def action_dismiss(self):
        missing_note = self.filtered(lambda issue: not (issue.resolution_note or "").strip())
        if missing_note:
            raise UserError(_("Add a resolution note before dismissing a quality row."))
        now = fields.Datetime.now()
        for issue in self:
            issue.write(
                {
                    "state": "dismissed",
                    "resolved_at": now,
                    "accepted_fact_key": fact_key(
                        issue.issue_type,
                        issue.details,
                        issue.severity,
                        issue.work_lane,
                    ),
                }
            )
        return True

    def action_block(self):
        self.write({"state": "blocked"})
        return True

    def action_reopen(self):
        self.write({"state": "open", "resolved_at": False, "accepted_fact_key": False})

    def action_open_product(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.product_tmpl_id.display_name,
            "res_model": "product.template",
            "view_mode": "form",
            "res_id": self.product_tmpl_id.id,
        }

    def action_open_sourcing(self):
        self.ensure_one()
        if not self.sourcing_queue_id:
            raise UserError(_("This product has no Sparex sourcing row yet."))
        return {
            "type": "ir.actions.act_window",
            "name": self.sourcing_queue_id.display_name,
            "res_model": "southern.sparex.sourcing.queue",
            "view_mode": "form",
            "res_id": self.sourcing_queue_id.id,
        }

    def action_open_discovery(self):
        self.ensure_one()
        if not self.discovery_item_id:
            raise UserError(_("This product has no Sparex discovery row."))
        return {
            "type": "ir.actions.act_window",
            "name": self.discovery_item_id.display_name,
            "res_model": "southern.sparex.discovery.item",
            "view_mode": "form",
            "res_id": self.discovery_item_id.id,
        }

    def action_open_evidence(self):
        self.ensure_one()
        if not self.evidence_queue_id:
            raise UserError(_("This product has no open evidence-queue row."))
        return {
            "type": "ir.actions.act_window",
            "name": self.evidence_queue_id.display_name,
            "res_model": "southern.parts.evidence.queue",
            "view_mode": "form",
            "res_id": self.evidence_queue_id.id,
        }

    def action_refresh_selected_products(self):
        for company in self.mapped("company_id"):
            product_ids = list(
                dict.fromkeys(
                    self.filtered(lambda issue: issue.company_id == company).mapped("product_tmpl_id").ids
                )
            )
            self.with_company(company).refresh_quality_queue(
                limit=min(len(product_ids), QUALITY_BATCH_LIMIT) or 1,
                product_ids=product_ids[:QUALITY_BATCH_LIMIT],
            )
        return {
            "type": "ir.actions.client",
            "tag": "reload",
        }

    @api.model
    def _product_findings(self, product, duplicate_counts):
        price = product.list_price or 0.0
        cost = product.standard_price or 0.0
        reference = "".join((product.default_code or "").upper().split())
        is_sparex = reference.startswith("S.")
        sourcing_rows = (
            product.southern_sparex_sourcing_ids.filtered(lambda row: row.company_id == self.env.company)
            if is_sparex
            else self.env["southern.sparex.sourcing.queue"]
        )
        verified_supplier_costs = sourcing_rows.filtered(
            lambda row: row.supplier_price > 0
            and row.state
            in (
                "cost_approved",
                "cost_applied",
                "retail_approved",
                "publication_ready",
            )
        ).mapped("supplier_price")
        verified_supplier_cost = min(verified_supplier_costs) if verified_supplier_costs else 0.0
        evidence_count = sum(
            getattr(product, field_name, 0) or 0
            for field_name in (
                "southern_specification_count",
                "southern_fitment_count",
                "southern_oem_reference_count",
                "southern_catalog_page_count",
            )
        )
        return classify_product_quality(
            price=price,
            cost=cost,
            verified_supplier_cost=verified_supplier_cost,
            is_sparex=is_sparex,
            published=bool(product.website_published),
            source_url=product.southern_source_url or "",
            evidence_count=evidence_count,
            has_website_category=bool(product.public_categ_ids),
            has_image=bool(product.image_128),
            description_ready=customer_description_ready(product),
            sparex_publication_eligible=any(sourcing_rows.mapped("publication_eligible")),
            reference=reference,
            duplicate_count=duplicate_counts.get(reference, 0),
        )

    @api.model
    def _issue_codes(self, product, duplicate_counts):
        return [finding.issue_type for finding in self._product_findings(product, duplicate_counts)]

    @api.model
    def _product_company_domain(self):
        return [("company_id", "in", [False, self.env.company.id])]

    @api.model
    def _duplicate_counts_for(self, products):
        Product = self.env["product.template"].with_context(active_test=False, bin_size=True)
        raw_references = [reference for reference in products.mapped("default_code") if reference]
        if not raw_references:
            return Counter()
        duplicate_candidates = Product.search(
            self._product_company_domain() + [("default_code", "in", raw_references)]
        )
        return Counter(
            "".join((reference or "").upper().split())
            for reference in duplicate_candidates.mapped("default_code")
            if reference
        )

    @api.model
    def _unseen_published_product_ids(self, Product, company_domain, limit):
        """Published products with no quality row for the current company."""
        if limit <= 0:
            return []
        seen_ids = self.search([("company_id", "=", self.env.company.id)]).mapped(
            "product_tmpl_id"
        ).ids
        domain = company_domain + [("website_published", "=", True)]
        if seen_ids:
            domain = domain + [("id", "not in", seen_ids)]
        return Product.search(domain, order="id desc", limit=limit).ids

    @api.model
    def _search_refresh_products(self, limit, after_id):
        Product = self.env["product.template"].with_context(active_test=False, bin_size=True)
        limit = int(limit or QUALITY_BATCH_LIMIT)
        company_domain = self._product_company_domain()
        published_budget = min(QUALITY_PRIORITY_PUBLISHED_LIMIT, limit)
        unseen_published_ids = self._unseen_published_product_ids(
            Product,
            company_domain,
            min(QUALITY_PRIORITY_UNSEEN_PUBLISHED_LIMIT, published_budget),
        )
        recent_published = Product.search(
            company_domain + [("website_published", "=", True)],
            order="write_date desc, id desc",
            limit=published_budget,
        )
        published_ids = prioritize_published_refresh_ids(
            unseen_published_ids, recent_published.ids, published_budget
        )
        open_issue_products = self.search(
            [
                ("company_id", "=", self.env.company.id),
                ("state", "in", ["open", "in_progress", "blocked"]),
            ],
            order="last_detected_at, id",
            limit=QUALITY_PRIORITY_OPEN_LIMIT * 3,
        ).mapped("product_tmpl_id")
        used = set(published_ids)
        stale_open_ids = []
        for product in open_issue_products:
            if product.id in used:
                continue
            used.add(product.id)
            stale_open_ids.append(product.id)
            if len(stale_open_ids) >= QUALITY_PRIORITY_OPEN_LIMIT:
                break
        remaining = max(limit - len(published_ids) - len(stale_open_ids), 0)
        cursor = Product.browse()
        if remaining:
            cursor = Product.search(
                company_domain + [("id", ">", int(after_id or 0))],
                order="id",
                limit=remaining,
            )
            if not cursor and after_id:
                cursor = Product.search(company_domain, order="id", limit=remaining)
        product_ids = merge_quality_refresh_ids(
            published_ids, stale_open_ids, cursor.ids, limit
        )
        return Product.browse(product_ids), cursor[-1].id if cursor else int(after_id or 0)

    @api.model
    def refresh_quality_queue(self, limit=None, after_id=0, product_ids=None):
        Product = self.env["product.template"].with_context(active_test=False, bin_size=True)
        if product_ids is not None:
            products = Product.browse(list(product_ids)).exists()
            last_product_id = int(after_id or 0)
        else:
            products, last_product_id = self._search_refresh_products(limit, after_id)
        duplicate_counts = self._duplicate_counts_for(products)
        now = fields.Datetime.now()
        detected = set()
        created = updated = resolved = skipped_dismissed = 0
        existing_by_key = defaultdict(lambda: self.browse())
        dismissed_by_key = defaultdict(lambda: self.browse())
        if products:
            for issue in self.search(
                [
                    ("company_id", "=", self.env.company.id),
                    ("product_tmpl_id", "in", products.ids),
                    ("state", "in", ["open", "in_progress", "blocked", "dismissed"]),
                ]
            ):
                key = (issue.product_tmpl_id.id, issue.issue_type)
                if issue.state == "dismissed":
                    dismissed_by_key[key] |= issue
                else:
                    existing_by_key[key] |= issue
        for product in products:
            for finding in self._product_findings(product, duplicate_counts):
                key = (product.id, finding.issue_type, self.env.company.id)
                detected.add(key)
                values = {
                    "last_detected_at": now,
                    "details": finding.details,
                    "severity": finding.severity,
                    "work_lane": finding.work_lane,
                    "next_action": finding.next_action,
                }
                existing = existing_by_key[(product.id, finding.issue_type)]
                dismissed = dismissed_by_key[(product.id, finding.issue_type)]
                current = existing[:1]
                extras = existing[1:]
                if current:
                    current.write(values)
                    updated += 1
                    if extras:
                        extras.write(
                            {
                                "state": "resolved",
                                "resolved_at": now,
                                "resolution_note": _("Duplicate quality row closed by the quality refresh."),
                            }
                        )
                        resolved += len(extras)
                    continue
                latest_dismissed = dismissed.sorted("id", reverse=True)[:1]
                if latest_dismissed and not dismissed_should_reopen(
                    {
                        "details": latest_dismissed.details,
                        "severity": latest_dismissed.severity,
                        "work_lane": latest_dismissed.work_lane,
                        "accepted_fact_key": latest_dismissed.accepted_fact_key,
                    },
                    finding,
                ):
                    latest_dismissed.write(
                        dict(values, accepted_fact_key=finding_fact_key(finding))
                    )
                    skipped_dismissed += 1
                    continue
                if latest_dismissed:
                    latest_dismissed.write(
                        dict(values, state="open", resolved_at=False, accepted_fact_key=False)
                    )
                    updated += 1
                else:
                    self.create(
                        dict(
                            values,
                            product_tmpl_id=product.id,
                            company_id=self.env.company.id,
                            issue_type=finding.issue_type,
                        )
                    )
                    created += 1
        open_issues = self.search(
            [
                ("company_id", "=", self.env.company.id),
                ("product_tmpl_id", "in", products.ids),
                ("state", "in", ["open", "in_progress", "blocked"]),
            ]
        )
        stale = open_issues.filtered(
            lambda issue: (issue.product_tmpl_id.id, issue.issue_type, issue.company_id.id) not in detected
        )
        if stale:
            stale.write(
                {
                    "state": "resolved",
                    "resolved_at": now,
                    "resolution_note": _("Automatically resolved by the quality refresh."),
                }
            )
            resolved += len(stale)
        return {
            "created": created,
            "updated": updated,
            "resolved": resolved,
            "skipped_dismissed": skipped_dismissed,
            "scanned": len(products),
            "last_product_id": last_product_id,
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


class ProductTemplate(models.Model):
    _inherit = "product.template"

    southern_quality_issue_ids = fields.One2many(
        "southern.product.quality.issue",
        "product_tmpl_id",
        string="Product Quality Issues",
    )
    southern_open_quality_issue_count = fields.Integer(
        compute="_compute_southern_open_quality_issue_count",
    )

    def _compute_southern_open_quality_issue_count(self):
        counts = dict.fromkeys(self.ids, 0)
        if self.ids:
            grouped = self.env["southern.product.quality.issue"]._read_group(
                [
                    ("product_tmpl_id", "in", self.ids),
                    ("state", "in", ["open", "in_progress", "blocked"]),
                    ("issue_type", "!=", "publication_ready"),
                ],
                ["product_tmpl_id"],
                ["__count"],
            )
            for product, count in grouped:
                counts[product.id] = count
        for product in self:
            product.southern_open_quality_issue_count = counts.get(product.id, 0)

    def action_open_southern_quality_issues(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Product Master Quality"),
            "res_model": "southern.product.quality.issue",
            "view_mode": "list,form",
            "domain": [("product_tmpl_id", "=", self.id)],
            "context": {
                "default_product_tmpl_id": self.id,
                "search_default_open": 1,
            },
        }
