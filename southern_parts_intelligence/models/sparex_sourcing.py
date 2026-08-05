import re
from datetime import timedelta
from urllib.parse import urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .catalog_agents import customer_description_ready, sales_price_blocker

SPAREX_SOURCE_HOSTS = {"us.sparex.com"}


class SouthernSparexSourcingQueue(models.Model):
    _name = "southern.sparex.sourcing.queue"
    _description = "Sparex Supplier Sourcing Queue"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, next_attempt_at, id"

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    product_tmpl_id = fields.Many2one(
        "product.template",
        required=True,
        ondelete="cascade",
        index=True,
        tracking=True,
    )
    sku = fields.Char(related="product_tmpl_id.default_code", store=True, index=True)
    supplier_id = fields.Many2one(
        "res.partner",
        required=True,
        domain="[('supplier_rank', '>', 0)]",
        index=True,
        tracking=True,
    )
    supplierinfo_id = fields.Many2one("product.supplierinfo", readonly=True, copy=False)
    priority = fields.Selection(
        [("0", "Low"), ("1", "Normal"), ("2", "High"), ("3", "Urgent")],
        default="1",
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("cooldown", "Cooldown"),
            ("manual_review", "Manual Review"),
            ("evidence_found", "Evidence Found"),
            ("cost_approved", "Supplier Cost Approved"),
            ("cost_applied", "Supplier Cost Applied"),
            ("retail_approved", "Retail Approved"),
            ("publication_ready", "Publication Ready"),
            ("rejected", "Rejected"),
        ],
        default="queued",
        required=True,
        tracking=True,
        index=True,
    )
    supplier_price = fields.Monetary(currency_field="currency_id", tracking=True)
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    price_effective_at = fields.Datetime()
    evidence_url = fields.Char(tracking=True)
    evidence_sha256 = fields.Char(readonly=True, copy=False)
    evidence_schema_version = fields.Char(default="1.0", required=True)
    evidence_retrieved_at = fields.Datetime(readonly=True, copy=False)
    parser_version = fields.Char(readonly=True, copy=False)
    source_run_id = fields.Char(index=True, readonly=True, copy=False)
    source_artifact_uri = fields.Char(readonly=True, copy=False)
    source_input_sha256 = fields.Char(readonly=True, copy=False)
    attempt_count = fields.Integer(default=0, readonly=True)
    last_attempt_at = fields.Datetime(readonly=True)
    next_attempt_at = fields.Datetime(index=True)
    failure_code = fields.Char(index=True, readonly=True)
    failure_reason = fields.Char(readonly=True)
    required_margin_percent = fields.Float(default=35.0)
    proposed_retail_price = fields.Monetary(currency_field="currency_id")
    approved_retail_price = fields.Monetary(currency_field="currency_id", tracking=True)
    approved_cost_by_id = fields.Many2one("res.users", readonly=True)
    approved_cost_at = fields.Datetime(readonly=True)
    approved_retail_by_id = fields.Many2one("res.users", readonly=True)
    approved_retail_at = fields.Datetime(readonly=True)
    publication_eligible = fields.Boolean(compute="_compute_publication_eligible", store=True, index=True)
    publication_blockers = fields.Text(compute="_compute_publication_eligible", store=True)

    _product_company_unique = models.Constraint(
        "unique(product_tmpl_id, company_id)",
        "Each Sparex product can have only one sourcing-control row per company.",
    )

    @api.depends("sku", "supplier_id")
    def _compute_name(self):
        for row in self:
            row.name = "%s / %s" % (row.sku or _("No SKU"), row.supplier_id.display_name or _("No Supplier"))

    def _source_is_exact_sparex(self):
        self.ensure_one()
        parsed = urlsplit(self.evidence_url or "")
        host = (parsed.hostname or "").casefold().rstrip(".")
        return parsed.scheme.casefold() == "https" and host in SPAREX_SOURCE_HOSTS

    def _evidence_is_fresh(self):
        self.ensure_one()
        return bool(
            self.evidence_retrieved_at
            and self.evidence_retrieved_at >= fields.Datetime.now() - timedelta(days=30)
        )

    @api.depends(
        "state",
        "supplier_price",
        "currency_id",
        "evidence_url",
        "evidence_sha256",
        "evidence_retrieved_at",
        "approved_retail_price",
        "required_margin_percent",
        "product_tmpl_id.active",
        "product_tmpl_id.sale_ok",
        "product_tmpl_id.public_categ_ids",
        "product_tmpl_id.image_1920",
        "product_tmpl_id.list_price",
        "product_tmpl_id.description_ecommerce",
        "product_tmpl_id.website_description",
        "product_tmpl_id.description_sale",
    )
    def _compute_publication_eligible(self):
        now = fields.Datetime.now()
        for row in self:
            product = row.product_tmpl_id
            blockers = []
            if not (row.sku or "").upper().startswith("S."):
                blockers.append("SKU is not an exact Sparex S.% reference")
            if row.state not in ("retail_approved", "publication_ready"):
                blockers.append("Supplier cost and retail price are not both approved")
            if row.supplier_price <= 0:
                blockers.append("Missing positive supplier cost")
            if row.currency_id != row.company_id.currency_id:
                blockers.append("Supplier cost currency requires review")
            if not row._source_is_exact_sparex():
                blockers.append("Missing exact HTTPS Sparex evidence URL")
            if not re.fullmatch(r"[0-9a-f]{64}", (row.evidence_sha256 or "").casefold()):
                blockers.append("Missing valid evidence SHA-256")
            if not row.evidence_retrieved_at or row.evidence_retrieved_at < now - timedelta(days=30):
                blockers.append("Supplier evidence is older than 30 days")
            required_price = (
                row.supplier_price / (1.0 - row.required_margin_percent / 100.0)
                if row.supplier_price > 0 and 0 <= row.required_margin_percent < 100
                else 0.0
            )
            if row.approved_retail_price <= 1.49:
                blockers.append("Approved retail price is not customer-ready")
            elif required_price and row.approved_retail_price + 0.005 < required_price:
                blockers.append("Approved retail price is below the required margin")
            price_blocker = sales_price_blocker(product, row.supplier_price)
            if price_blocker:
                blockers.append(price_blocker.replace("_", " ").capitalize())
            if not product.active:
                blockers.append("Product is archived")
            if not product.sale_ok:
                blockers.append("Product is not saleable")
            if not product.public_categ_ids:
                blockers.append("Missing website category")
            if not product.image_1920:
                blockers.append("Missing image")
            if not customer_description_ready(product):
                blockers.append("Missing customer-facing description")
            row.publication_eligible = not blockers
            row.publication_blockers = "; ".join(blockers)

    @api.constrains(
        "supplier_price",
        "required_margin_percent",
        "proposed_retail_price",
        "approved_retail_price",
        "evidence_sha256",
        "source_input_sha256",
    )
    def _check_contract(self):
        sha_pattern = re.compile(r"^[0-9a-f]{64}$")
        for row in self:
            if row.supplier_price < 0 or row.proposed_retail_price < 0 or row.approved_retail_price < 0:
                raise ValidationError(_("Prices cannot be negative."))
            if not 0 <= row.required_margin_percent < 100:
                raise ValidationError(_("Required margin must be between 0 and 100 percent."))
            for value in (row.evidence_sha256, row.source_input_sha256):
                if value and not sha_pattern.fullmatch(value.casefold()):
                    raise ValidationError(_("Evidence hashes must be SHA-256 hexadecimal values."))

    def action_approve_supplier_cost(self):
        for row in self:
            if row.state != "evidence_found":
                raise UserError(_("Supplier evidence must be found before approval."))
            if row.supplier_price <= 0:
                raise UserError(_("A positive supplier price is required."))
            if row.currency_id != row.company_id.currency_id:
                raise UserError(_("Supplier price currency must match the company currency."))
            if not row._source_is_exact_sparex() or not row._evidence_is_fresh():
                raise UserError(_("Exact, current Sparex evidence is required."))
            if not re.fullmatch(r"[0-9a-f]{64}", (row.evidence_sha256 or "").casefold()):
                raise UserError(_("A valid evidence SHA-256 is required."))
            row.write(
                {
                    "state": "cost_approved",
                    "approved_cost_by_id": self.env.user.id,
                    "approved_cost_at": fields.Datetime.now(),
                    "failure_code": False,
                    "failure_reason": False,
                }
            )
        return True

    def action_apply_supplier_cost(self):
        SupplierInfo = self.env["product.supplierinfo"].sudo()
        for row in self:
            if row.state != "cost_approved" or not row.approved_cost_at:
                raise UserError(_("Supplier cost must be approved before it can be applied."))
            values = {
                "partner_id": row.supplier_id.id,
                "product_tmpl_id": row.product_tmpl_id.id,
                "product_code": row.sku,
                "price": row.supplier_price,
                "min_qty": 1.0,
            }
            if "currency_id" in SupplierInfo._fields:
                values["currency_id"] = row.currency_id.id
            supplierinfo = row.supplierinfo_id.exists() or SupplierInfo.search(
                [
                    ("partner_id", "=", row.supplier_id.id),
                    ("product_tmpl_id", "=", row.product_tmpl_id.id),
                ],
                limit=1,
            )
            if supplierinfo:
                supplierinfo.write(values)
            else:
                supplierinfo = SupplierInfo.create(values)
            row.write({"supplierinfo_id": supplierinfo.id, "state": "cost_applied"})
        return True

    def action_approve_retail(self):
        for row in self:
            if row.state != "cost_applied":
                raise UserError(_("Apply the approved supplier cost before approving retail."))
            required = row.supplier_price / (1.0 - row.required_margin_percent / 100.0)
            approved = row.approved_retail_price or row.proposed_retail_price
            if approved + 0.005 < required:
                raise UserError(_("Retail price does not satisfy the required margin."))
            row.write(
                {
                    "approved_retail_price": approved,
                    "state": "retail_approved",
                    "approved_retail_by_id": self.env.user.id,
                    "approved_retail_at": fields.Datetime.now(),
                }
            )
        return True

    def action_apply_approved_retail(self):
        for row in self:
            if row.state != "retail_approved" or not row.publication_eligible:
                raise UserError(_("The complete publication gate must pass before applying retail."))
            row.product_tmpl_id.sudo().write({"list_price": row.approved_retail_price})
            row.state = "publication_ready"
        return True

    def action_manual_review(self):
        self.write({"state": "manual_review", "next_attempt_at": False})
        return True

    @api.model
    def record_external_attempt(self, product_id, values=None):
        """Idempotently record one external sourcing result without touching standard cost."""
        values = dict(values or {})
        row = self.search(
            [("product_tmpl_id", "=", int(product_id)), ("company_id", "=", self.env.company.id)],
            limit=1,
        )
        if not row:
            supplier_id = int(values.pop("supplier_id", 0) or 0)
            if not supplier_id:
                raise UserError(_("supplier_id is required for a new sourcing row."))
            row = self.create({"product_tmpl_id": int(product_id), "supplier_id": supplier_id})
        attempts = row.attempt_count + 1
        success = bool(values.get("supplier_price") and values.get("evidence_sha256"))
        update = {
            "attempt_count": attempts,
            "last_attempt_at": fields.Datetime.now(),
            "source_run_id": values.get("source_run_id"),
            "source_artifact_uri": values.get("source_artifact_uri"),
            "source_input_sha256": values.get("source_input_sha256"),
            "parser_version": values.get("parser_version"),
        }
        if success:
            update.update(
                {
                    "state": "evidence_found",
                    "supplier_price": values.get("supplier_price"),
                    "currency_id": values.get("currency_id") or row.currency_id.id,
                    "price_effective_at": values.get("price_effective_at") or fields.Datetime.now(),
                    "evidence_url": values.get("evidence_url"),
                    "evidence_sha256": values.get("evidence_sha256"),
                    "evidence_schema_version": values.get("evidence_schema_version") or "1.0",
                    "evidence_retrieved_at": values.get("evidence_retrieved_at") or fields.Datetime.now(),
                    "proposed_retail_price": values.get("proposed_retail_price") or 0.0,
                    "next_attempt_at": False,
                    "failure_code": False,
                    "failure_reason": False,
                }
            )
        else:
            manual = attempts >= 3 or values.get("failure_code") == "ambiguous_price"
            update.update(
                {
                    "state": "manual_review" if manual else "cooldown",
                    "next_attempt_at": False if manual else fields.Datetime.now() + timedelta(days=7),
                    "failure_code": values.get("failure_code") or "no_exact_price",
                    "failure_reason": values.get("failure_reason") or _("No exact supplier price was accepted."),
                }
            )
            if values.get("evidence_url") and values.get("evidence_sha256"):
                update.update(
                    {
                        "evidence_url": values.get("evidence_url"),
                        "evidence_sha256": values.get("evidence_sha256"),
                        "evidence_schema_version": values.get("evidence_schema_version") or "1.0",
                        "evidence_retrieved_at": values.get("evidence_retrieved_at") or fields.Datetime.now(),
                    }
                )
        row.write(update)
        return row.id


class ProductTemplate(models.Model):
    _inherit = "product.template"

    southern_sparex_sourcing_ids = fields.One2many(
        "southern.sparex.sourcing.queue",
        "product_tmpl_id",
        string="Sparex Sourcing",
    )
    southern_sparex_publication_eligible = fields.Boolean(
        compute="_compute_southern_sparex_publication_eligible",
        store=True,
        index=True,
    )

    @api.depends("southern_sparex_sourcing_ids.publication_eligible")
    def _compute_southern_sparex_publication_eligible(self):
        for product in self:
            product.southern_sparex_publication_eligible = any(
                product.southern_sparex_sourcing_ids.mapped("publication_eligible")
            )
