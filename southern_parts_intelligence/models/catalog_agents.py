import hashlib
import json
import math
import re
import uuid
from urllib.parse import unquote, urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

CATALOG_AGENT_CODES = [
    ("coordinator", "Catalog Coordinator"),
    ("sparex_discovery", "Sparex Discovery Agent"),
    ("odoo_match", "Odoo Match Agent"),
    ("product_verification", "Product Verification Agent"),
    ("website_release", "Website Release Agent"),
]
CATALOG_AGENT_SEQUENCE = [code for code, _name in CATALOG_AGENT_CODES]
CATALOG_AGENT_TOOLS = {
    "coordinator": "route_catalog_task",
    "sparex_discovery": "verify_sparex_listing",
    "odoo_match": "inspect_odoo_match",
    "product_verification": "evaluate_product_readiness",
    "website_release": "evaluate_release_gate",
}
EXPECTED_DECISIONS = {
    "coordinator": "continue",
    "sparex_discovery": "continue",
    "odoo_match": "continue",
    "product_verification": "ready_for_release",
    "website_release": "ready_for_release",
}
MAX_AGENT_BATCH = 50
SPAREX_HOSTS = {"us.sparex.com"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SPAREX_SKU_PATTERN = re.compile(r"^S[.\s-]?0*(\d+)$", re.IGNORECASE)
SPAREX_REFERENCE_PREFIX_PATTERN = re.compile(r"^S[.\s-]", re.IGNORECASE)
MIN_CUSTOMER_READY_PRICE = 1.49
PLACEHOLDER_DESCRIPTION_MARKERS = (
    "internal catalog record",
    "not published to the website until",
)
REQUIRED_COST_PLUS_MARGIN_PERCENT = 35.0


def normalized_sparex_sku(value):
    match = SPAREX_SKU_PATTERN.fullmatch((value or "").strip())
    if not match:
        return ""
    return f"S.{int(match.group(1))}"


def is_sparex_catalog_reference(value):
    return bool(SPAREX_REFERENCE_PREFIX_PATTERN.match((value or "").strip()))


def exact_sparex_url(value, normalized_sku):
    parsed = urlsplit((value or "").strip())
    if parsed.scheme.casefold() != "https":
        return False
    if (parsed.hostname or "").casefold().rstrip(".") not in SPAREX_HOSTS:
        return False
    normalized = normalized_sparex_sku(normalized_sku)
    if not normalized:
        return False
    digits = normalized.split(".", 1)[1]
    return bool(re.search(rf"(?<!\d)0*{re.escape(digits)}(?!\d)", unquote(parsed.path), re.IGNORECASE))


def canonical_sha256(value):
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def customer_description_ready(product):
    descriptions = " ".join(
        str(product[field_name] or "")
        for field_name in ("description_ecommerce", "website_description", "description_sale")
        if field_name in product._fields
    )
    plain_text = re.sub(r"<[^>]+>", " ", descriptions)
    normalized = " ".join(plain_text.casefold().split())
    return bool(normalized) and not any(marker in normalized for marker in PLACEHOLDER_DESCRIPTION_MARKERS)


def sales_price_blocker(product, supplier):
    sale_price = float(product.list_price or 0.0)
    if isinstance(supplier, (int, float)):
        supplier_cost = float(supplier)
    else:
        supplier_cost = float(supplier.price or 0.0) if supplier else 0.0
    verified_low_cost_plus = bool(
        product.southern_price_basis == "cost_plus"
        and float(product.southern_cost_plus_margin_percent or 0.0) > 0
        and supplier_cost > 0
        and sale_price > supplier_cost
    )
    if sale_price <= MIN_CUSTOMER_READY_PRICE and not verified_low_cost_plus:
        return "placeholder_sales_price"
    if supplier_cost > 0 and sale_price <= supplier_cost:
        return "sales_price_not_above_supplier_cost"
    return ""


def exact_dealer_cost_evidence_ready(product):
    source_url = (product.southern_source_url or "").strip()
    source_sha = hashlib.sha256(source_url.encode("utf-8")).hexdigest()
    evidence = product.env["southern.sparex.discovery.item"].sudo().search(
        [
            ("matched_product_id", "=", product.id),
            ("normalized_sku", "=", normalized_sparex_sku(product.default_code)),
            ("source_url_sha256", "=", source_sha),
            ("cost_recovery_state", "=", "resolved"),
            ("cost_evidence_url_sha256", "=", source_sha),
            ("cost_evidence_sha256", "!=", False),
            ("cost_recovered_at", "!=", False),
        ],
        order="id desc",
        limit=1,
    )
    return bool(evidence and SHA256_PATTERN.fullmatch((evidence.cost_evidence_sha256 or "").casefold()))


def pricing_basis_blockers(product, supplier):
    supplier_cost = float(supplier.price or 0.0) if supplier else 0.0
    sale_price = float(product.list_price or 0.0)
    basis = product.southern_price_basis or "none"
    if basis == "retail_evidence":
        return []
    if basis != "cost_plus":
        return ["missing_verified_price_basis"]
    blockers = []
    margin = float(product.southern_cost_plus_margin_percent or 0.0)
    if abs(margin - REQUIRED_COST_PLUS_MARGIN_PERCENT) > 1e-9:
        blockers.append("cost_plus_margin_not_35_percent")
    if supplier_cost > 0:
        expected = math.ceil((supplier_cost / 0.65) * 100.0) / 100.0
        if abs(sale_price - expected) > 0.01:
            blockers.append("cost_plus_price_not_35_percent_margin")
    return blockers


def sparex_publication_blockers(product, supplier, normalized_sku=None):
    normalized = normalized_sparex_sku(normalized_sku or product.default_code)
    blockers = []
    if not normalized:
        blockers.append("invalid_sparex_sku")
    if not product.active:
        blockers.append("product_archived")
    if not product.sale_ok:
        blockers.append("product_not_saleable")
    if product.southern_quote_only:
        blockers.append("quote_only_not_publishable")
    else:
        if not supplier:
            blockers.append("missing_positive_sparex_cost")
        if float(product.standard_price or 0.0) <= 0:
            blockers.append("missing_positive_standard_cost")
        elif supplier and abs(float(product.standard_price) - float(supplier.price or 0.0)) > 0.000001:
            blockers.append("standard_cost_not_equal_verified_supplier_cost")
        if supplier and not exact_dealer_cost_evidence_ready(product):
            blockers.append("missing_exact_dealer_cost_evidence")
        price_blocker = sales_price_blocker(product, supplier)
        if price_blocker:
            blockers.append(price_blocker)
        blockers.extend(pricing_basis_blockers(product, supplier))
    if not exact_sparex_url(product.southern_source_url, normalized):
        blockers.append("missing_exact_sparex_url")
    if not product.image_1920:
        blockers.append("missing_image")
    if not product.public_categ_ids:
        blockers.append("missing_website_category")
    if not customer_description_ready(product):
        blockers.append("missing_customer_description")
    return blockers


class ProductTemplate(models.Model):
    _inherit = "product.template"

    @api.constrains(
        "active",
        "default_code",
        "description_ecommerce",
        "description_sale",
        "image_1920",
        "is_published",
        "list_price",
        "public_categ_ids",
        "sale_ok",
        "southern_cost_plus_margin_percent",
        "southern_price_basis",
        "southern_quote_only",
        "southern_source_url",
        "standard_price",
        "website_description",
        "website_published",
    )
    def _check_sparex_publication_readiness(self):
        SupplierInfo = self.env["product.supplierinfo"].sudo()
        for product in self.filtered(
            lambda record: (record.website_published or record.is_published)
            and is_sparex_catalog_reference(record.default_code)
        ):
            supplier = SupplierInfo.search(
                [
                    ("product_tmpl_id", "=", product.id),
                    ("partner_id.name", "=ilike", "Sparex"),
                    ("price", ">", 0),
                ],
                order="id",
                limit=1,
            )
            blockers = sparex_publication_blockers(product, supplier)
            if blockers:
                raise ValidationError(
                    _("Sparex product %(sku)s cannot be published: %(blockers)s")
                    % {"sku": product.default_code, "blockers": ", ".join(blockers)}
                )


class SouthernCatalogAgent(models.Model):
    _name = "southern.catalog.agent"
    _description = "Southern Catalog Agent"
    _inherit = ["mail.thread"]  # noqa: RUF012 - Odoo model metadata
    _order = "sequence, id"

    name = fields.Char(required=True, tracking=True)
    code = fields.Selection(CATALOG_AGENT_CODES, required=True, index=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    model_name = fields.Char(
        default="gpt-5.6",
        required=True,
        help="OpenAI model used by the external Agents SDK worker. API credentials are never stored in Odoo.",
    )
    tool_name = fields.Char(compute="_compute_tool_name")
    instructions = fields.Text(required=True)
    batch_size = fields.Integer(default=MAX_AGENT_BATCH, required=True)
    throttle_seconds = fields.Float(default=3.0, required=True)
    ai_enabled = fields.Boolean(
        default=False,
        tracking=True,
        help="Enables the external OpenAI worker. Agents remain unable to write products.",
    )
    internal_cron_enabled = fields.Boolean(
        default=False,
        tracking=True,
        help="The external non-overlapping system service owns scheduling.",
    )
    task_ids = fields.One2many("southern.catalog.agent.task", "agent_id")
    task_count = fields.Integer(compute="_compute_task_count")

    _code_company_unique = models.Constraint(
        "unique(code, company_id)", "Each company can have only one profile for each catalog agent."
    )

    @api.depends("task_ids")
    def _compute_task_count(self):
        for agent in self:
            agent.task_count = len(agent.task_ids)

    @api.depends("code")
    def _compute_tool_name(self):
        for agent in self:
            agent.tool_name = CATALOG_AGENT_TOOLS.get(agent.code, "")

    @api.constrains("batch_size", "throttle_seconds")
    def _check_limits(self):
        for agent in self:
            if not 1 <= agent.batch_size <= MAX_AGENT_BATCH:
                raise ValidationError(_("Catalog agent batches must contain between 1 and 50 records."))
            if agent.throttle_seconds < 3.0:
                raise ValidationError(_("Catalog agent source throttling cannot be less than 3 seconds."))

    def action_view_tasks(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Catalog Agent Tasks"),
            "res_model": "southern.catalog.agent.task",
            "view_mode": "list,form",
            "domain": [("agent_id", "=", self.id)],
            "context": {"default_agent_id": self.id},
        }


class SouthernCatalogAgentTask(models.Model):
    _name = "southern.catalog.agent.task"
    _description = "Southern Catalog Agent Task"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model metadata
    _order = "priority desc, create_date, id"

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    agent_id = fields.Many2one("southern.catalog.agent", required=True, ondelete="restrict", index=True, tracking=True)
    agent_code = fields.Selection(related="agent_id.code", store=True, index=True)
    parent_task_id = fields.Many2one("southern.catalog.agent.task", readonly=True, index=True, ondelete="restrict")
    root_task_id = fields.Many2one("southern.catalog.agent.task", readonly=True, index=True, ondelete="restrict")
    priority = fields.Selection(
        [("0", "Low"), ("1", "Normal"), ("2", "High"), ("3", "Urgent")],
        default="1",
        required=True,
        index=True,
    )
    state = fields.Selection(
        [
            ("queued", "Queued"),
            ("claimed", "Claimed"),
            ("completed", "Completed"),
            ("blocked", "Blocked"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="queued",
        required=True,
        tracking=True,
        index=True,
    )
    idempotency_key = fields.Char(required=True, copy=False, index=True, default=lambda self: uuid.uuid4().hex)
    external_sku = fields.Char(required=True, index=True, tracking=True)
    normalized_sku = fields.Char(readonly=True, index=True)
    product_tmpl_id = fields.Many2one("product.template", readonly=True, index=True)
    odoo_match_state = fields.Selection(
        [("unchecked", "Unchecked"), ("matched", "Matched"), ("missing", "Missing"), ("duplicate", "Duplicate")],
        default="unchecked",
        required=True,
        readonly=True,
        index=True,
    )
    has_positive_supplier_cost = fields.Boolean(readonly=True)
    has_positive_sales_price = fields.Boolean(readonly=True)
    has_exact_sparex_url = fields.Boolean(readonly=True)
    has_image = fields.Boolean(readonly=True)
    product_is_hidden = fields.Boolean(readonly=True)
    ready_to_publish = fields.Boolean(readonly=True, index=True)
    readiness_blockers = fields.Text(readonly=True)
    snapshot_sha256 = fields.Char(readonly=True, copy=False, index=True)
    dealer_cost_url_sha256 = fields.Char(readonly=True, copy=False)
    retail_price_url_sha256 = fields.Char(readonly=True, copy=False)
    source_artifact_uri = fields.Char(readonly=True, copy=False)
    source_artifact_sha256 = fields.Char(readonly=True, copy=False)
    input_json = fields.Text(readonly=True, copy=False)
    output_json = fields.Text(readonly=True, copy=False)
    result_sha256 = fields.Char(readonly=True, copy=False)
    worker_id = fields.Char(readonly=True, copy=False, index=True)
    claimed_at = fields.Datetime(readonly=True)
    finished_at = fields.Datetime(readonly=True)
    error_message = fields.Text(readonly=True)
    publication_state = fields.Selection(
        [
            ("not_applicable", "Not Applicable"),
            ("ready", "Ready"),
            ("published", "Published"),
            ("verified", "Publicly Verified"),
            ("rolled_back", "Rolled Back"),
            ("failed", "Failed"),
        ],
        default="not_applicable",
        required=True,
        readonly=True,
        index=True,
    )
    publication_fields_before_json = fields.Text(readonly=True, copy=False)
    publication_fields_after_json = fields.Text(readonly=True, copy=False)
    publication_public_url = fields.Char(readonly=True, copy=False)
    publication_verification_sha256 = fields.Char(readonly=True, copy=False)
    published_at = fields.Datetime(readonly=True)
    publication_verified_at = fields.Datetime(readonly=True)

    _idempotency_unique = models.Constraint(
        "unique(idempotency_key)", "A catalog agent task already exists for this idempotency key."
    )

    @api.depends("external_sku", "agent_id")
    def _compute_name(self):
        for task in self:
            task.name = "{} / {}".format(task.external_sku or _("No SKU"), task.agent_id.name or _("No Agent"))

    @api.constrains(
        "snapshot_sha256",
        "dealer_cost_url_sha256",
        "retail_price_url_sha256",
        "source_artifact_sha256",
        "result_sha256",
        "publication_verification_sha256",
    )
    def _check_hashes(self):
        for task in self:
            for value in (
                task.snapshot_sha256,
                task.dealer_cost_url_sha256,
                task.retail_price_url_sha256,
                task.source_artifact_sha256,
                task.result_sha256,
                task.publication_verification_sha256,
            ):
                if value and not SHA256_PATTERN.fullmatch(value.casefold()):
                    raise ValidationError(_("Catalog agent evidence hashes must be SHA-256 hexadecimal values."))

    def _find_exact_product(self):
        self.ensure_one()
        normalized = normalized_sparex_sku(self.external_sku)
        if not normalized:
            return normalized, self.env["product.template"]
        digits = normalized.split(".", 1)[1]
        candidates = (
            self.env["product.template"]
            .with_context(active_test=False)
            .search(["|", ("default_code", "ilike", f"S.{digits}"), ("default_code", "ilike", f"S{digits}")])
        )
        exact = candidates.filtered(lambda product: normalized_sparex_sku(product.default_code) == normalized)
        return normalized, exact

    @api.model
    def _positive_sparex_supplier(self, product):
        return (
            self.env["product.supplierinfo"]
            .sudo()
            .search(
                [
                    ("product_tmpl_id", "=", product.id),
                    ("partner_id.name", "=ilike", "Sparex"),
                    ("price", ">", 0),
                ],
                order="id",
                limit=1,
            )
        )

    @api.model
    def _current_discovery_item(self, product, normalized):
        return self.env["southern.sparex.discovery.item"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("normalized_sku", "=", normalized),
                ("matched_product_id", "=", product.id),
                ("reconciliation_state", "=", "current"),
                ("state", "=", "verified"),
                ("source_state", "=", "verified"),
                ("has_exact_sparex_url", "=", True),
                ("has_image", "=", True),
            ],
            limit=1,
        )

    @api.model
    def _product_invariants(self, product):
        supplier = self._positive_sparex_supplier(product)
        image = product.image_1920 or b""
        if isinstance(image, str):
            image = image.encode("ascii", errors="ignore")
        return {
            "product_id": product.id,
            "sku": normalized_sparex_sku(product.default_code),
            "list_price": product.list_price,
            "standard_price": product.standard_price,
            "source_url_sha256": hashlib.sha256((product.southern_source_url or "").encode("utf-8")).hexdigest(),
            "image_sha256": hashlib.sha256(image).hexdigest(),
            "supplier_cost_sha256": canonical_sha256(
                {"supplierinfo_id": supplier.id if supplier else None, "price": supplier.price if supplier else None}
            ),
        }

    def action_prepare_snapshot(self):
        for task in self:
            normalized, products = task._find_exact_product()
            blockers = []
            product = self.env["product.template"]
            match_state = "missing"
            if not normalized:
                blockers.append("invalid_sparex_sku")
            elif len(products) > 1:
                match_state = "duplicate"
                blockers.append("duplicate_odoo_sku")
            elif products:
                product = products[0]
                match_state = "matched"
            else:
                blockers.append("missing_in_odoo")

            has_cost = has_sales_price = has_url = has_image = is_hidden = False
            if product:
                supplier = task._positive_sparex_supplier(product)
                has_cost = bool(supplier)
                price_blocker = sales_price_blocker(product, supplier)
                has_sales_price = not bool(price_blocker)
                has_url = exact_sparex_url(product.southern_source_url, normalized)
                has_image = bool(product.image_1920)
                is_hidden = not bool(product.website_published)
                if not product.active:
                    blockers.append("product_archived")
                if not is_hidden:
                    blockers.append("already_published")
                if not has_cost:
                    blockers.append("missing_positive_sparex_cost")
                if price_blocker:
                    blockers.append(price_blocker)
                if not has_url:
                    blockers.append("missing_exact_sparex_url")
                if not has_image:
                    blockers.append("missing_image")
                if not product.public_categ_ids:
                    blockers.append("missing_website_category")
                if not customer_description_ready(product):
                    blockers.append("missing_customer_description")
                if not task._current_discovery_item(product, normalized):
                    blockers.append("missing_current_discovery_evidence")

            ready = bool(
                product
                and product.active
                and is_hidden
                and has_cost
                and has_sales_price
                and has_url
                and has_image
                and product.public_categ_ids
                and customer_description_ready(product)
                and task._current_discovery_item(product, normalized)
            )
            snapshot = {
                "schema_version": "1.1",
                "agent": task.agent_code,
                "task_id": task.id,
                "root_task_id": task.root_task_id.id or task.id,
                "sku": normalized or task.external_sku,
                "odoo_match_state": match_state,
                "product_id": product.id if product else None,
                "has_positive_supplier_cost": has_cost,
                "has_positive_sales_price": has_sales_price,
                "has_exact_sparex_url": has_url,
                "has_image": has_image,
                "product_is_hidden": is_hidden,
                "ready_to_publish": ready,
                "blockers": blockers,
            }
            snapshot_sha = canonical_sha256(snapshot)
            task.write(
                {
                    "normalized_sku": normalized,
                    "product_tmpl_id": product.id if product else False,
                    "odoo_match_state": match_state,
                    "has_positive_supplier_cost": has_cost,
                    "has_positive_sales_price": has_sales_price,
                    "has_exact_sparex_url": has_url,
                    "has_image": has_image,
                    "product_is_hidden": is_hidden,
                    "ready_to_publish": ready,
                    "readiness_blockers": ",".join(blockers),
                    "input_json": json.dumps(snapshot, sort_keys=True, separators=(",", ":")),
                    "snapshot_sha256": snapshot_sha,
                }
            )
        return True

    @api.model
    def queue_candidate(self, agent_code, external_sku, values=None):
        values = dict(values or {})
        agent = self.env["southern.catalog.agent"].search(
            [("code", "=", agent_code), ("company_id", "=", self.env.company.id), ("active", "=", True)],
            limit=1,
        )
        if not agent:
            raise UserError(_("No active catalog agent profile exists for %s.") % agent_code)
        idempotency_key = (values.get("idempotency_key") or "").strip()
        if not idempotency_key:
            raise UserError(_("Catalog agent tasks require an idempotency key."))
        existing = self.search([("idempotency_key", "=", idempotency_key)], limit=1)
        if existing:
            return existing.id
        allowed = {
            "priority",
            "dealer_cost_url_sha256",
            "retail_price_url_sha256",
            "source_artifact_uri",
            "source_artifact_sha256",
            "parent_task_id",
            "root_task_id",
        }
        task_values = {key: value for key, value in values.items() if key in allowed}
        task_values.update(
            {
                "agent_id": agent.id,
                "company_id": self.env.company.id,
                "external_sku": external_sku,
                "idempotency_key": idempotency_key,
            }
        )
        task = self.create(task_values)
        if not task.root_task_id:
            task.root_task_id = task.id
        task.action_prepare_snapshot()
        return task.id

    @api.model
    def _ready_products(self, limit=MAX_AGENT_BATCH):
        bounded = max(1, min(int(limit or MAX_AGENT_BATCH), MAX_AGENT_BATCH))
        discovery_items = self.env["southern.sparex.discovery.item"].search(
            [
                ("company_id", "=", self.env.company.id),
                ("reconciliation_state", "=", "current"),
                ("publication_candidate", "=", True),
                ("matched_product_id", "!=", False),
            ],
            order="readiness_refreshed_at, last_seen_at desc, id",
            limit=bounded * 4,
        )
        ready = self.env["product.template"]
        for item in discovery_items:
            product = item.matched_product_id.sudo()
            normalized = normalized_sparex_sku(product.default_code)
            if not normalized or not exact_sparex_url(product.southern_source_url, normalized):
                continue
            if not self._positive_sparex_supplier(product):
                continue
            if not self._current_discovery_item(product, normalized):
                continue
            active_pipeline = self.search_count(
                [
                    ("product_tmpl_id", "=", product.id),
                    ("publication_state", "in", ["ready", "published"]),
                ]
            )
            if active_pipeline:
                continue
            ready |= product
            if len(ready) >= bounded:
                break
        return ready

    @api.model
    def preview_ready_candidates(self, limit=MAX_AGENT_BATCH):
        return [
            {"product_id": product.id, "sku": normalized_sparex_sku(product.default_code)}
            for product in self._ready_products(limit)
        ]

    @api.model
    def seed_ready_candidates(self, worker_id, limit=MAX_AGENT_BATCH):
        agents = self.env["southern.catalog.agent"].search(
            [("company_id", "=", self.env.company.id), ("active", "=", True)]
        )
        enabled = set(agents.filtered("ai_enabled").mapped("code"))
        missing = [code for code in CATALOG_AGENT_SEQUENCE if code not in enabled]
        if missing:
            raise UserError(_("Enable all catalog agents before seeding: %s") % ", ".join(missing))
        task_ids = []
        for product in self._ready_products(limit):
            identity = {
                "schema_version": "1.1",
                "product_id": product.id,
                "sku": normalized_sparex_sku(product.default_code),
                "write_date": str(product.write_date or ""),
            }
            key = f"catalog-pipeline:{canonical_sha256(identity)}"
            task_id = self.queue_candidate(
                "coordinator",
                product.default_code,
                {"idempotency_key": key, "priority": "1"},
            )
            task = self.browse(task_id)
            task.action_prepare_snapshot()
            if (
                task.agent_code == "coordinator"
                and task.state in {"blocked", "failed", "cancelled"}
                and task.ready_to_publish
                and task.publication_state not in {"published", "verified"}
            ):
                task.write(
                    {
                        "state": "queued",
                        "output_json": False,
                        "result_sha256": False,
                        "claimed_at": False,
                        "finished_at": False,
                        "error_message": False,
                    }
                )
            task.worker_id = worker_id
            task_ids.append(task.id)
        return self.browse(task_ids).read(
            ["id", "root_task_id", "agent_code", "external_sku", "product_tmpl_id", "snapshot_sha256"]
        )

    @api.model
    def claim_tasks(self, agent_code, worker_id, limit=MAX_AGENT_BATCH):
        agent = self.env["southern.catalog.agent"].search(
            [("code", "=", agent_code), ("company_id", "=", self.env.company.id), ("active", "=", True)],
            limit=1,
        )
        if not agent or not agent.ai_enabled:
            raise UserError(_("The requested catalog agent is not enabled."))
        bounded_limit = max(1, min(int(limit or agent.batch_size), agent.batch_size, MAX_AGENT_BATCH))
        self.env.cr.execute(
            """
            SELECT id FROM southern_catalog_agent_task
             WHERE company_id = %s AND agent_id = %s AND state = 'queued'
             ORDER BY priority DESC, create_date, id
             FOR UPDATE SKIP LOCKED LIMIT %s
            """,
            [self.env.company.id, agent.id, bounded_limit],
        )
        tasks = self.browse([row[0] for row in self.env.cr.fetchall()])
        tasks.write({"state": "claimed", "worker_id": worker_id, "claimed_at": fields.Datetime.now()})
        return tasks.read(["id", "root_task_id", "agent_code", "external_sku", "input_json", "idempotency_key"])

    def _next_agent_code(self):
        self.ensure_one()
        index = CATALOG_AGENT_SEQUENCE.index(self.agent_code)
        return CATALOG_AGENT_SEQUENCE[index + 1] if index + 1 < len(CATALOG_AGENT_SEQUENCE) else None

    def _queue_handoff(self):
        self.ensure_one()
        next_code = self._next_agent_code()
        if not next_code:
            self.publication_state = "ready"
            return False
        handoff_key = f"catalog-handoff:{self.root_task_id.id}:{next_code}:{self.snapshot_sha256}"
        return self.queue_candidate(
            next_code,
            self.external_sku,
            {
                "idempotency_key": handoff_key,
                "priority": self.priority,
                "parent_task_id": self.id,
                "root_task_id": self.root_task_id.id,
                "source_artifact_uri": self.source_artifact_uri,
                "source_artifact_sha256": self.source_artifact_sha256,
            },
        )

    @api.model
    def record_external_result(self, task_id, output_json, result_sha256, state="completed"):
        if state not in {"completed", "blocked", "failed"}:
            raise UserError(_("Invalid catalog agent terminal state."))
        task = self.browse(int(task_id)).exists()
        if not task:
            raise UserError(_("The catalog agent task no longer exists."))
        if task.state in {"completed", "blocked", "failed", "cancelled"}:
            return task.id
        canonical_output = (output_json or "").strip()
        actual_sha = hashlib.sha256(canonical_output.encode("utf-8")).hexdigest()
        if not result_sha256 or actual_sha != result_sha256.casefold():
            raise UserError(_("Catalog agent result hash does not match the submitted output."))
        try:
            payload = json.loads(canonical_output)
        except (TypeError, ValueError) as exc:
            raise UserError(_("Catalog agent results must be valid JSON.")) from exc
        expected = EXPECTED_DECISIONS[task.agent_code]
        terminal_state = state
        error_message = False
        if state == "completed" and payload.get("decision") != expected:
            terminal_state = "blocked"
            error_message = _("Agent decision did not satisfy the deterministic stage contract.")
        task.write(
            {
                "state": terminal_state,
                "output_json": canonical_output,
                "result_sha256": actual_sha,
                "finished_at": fields.Datetime.now(),
                "error_message": error_message or (False if state != "failed" else _("External agent failed.")),
            }
        )
        if terminal_state == "completed":
            task.action_prepare_snapshot()
            if not task.ready_to_publish:
                task.write({"state": "blocked", "error_message": _("Readiness changed before handoff.")})
            else:
                task._queue_handoff()
        return task.id

    @api.model
    def prepare_publication_plan(self, worker_id, limit=MAX_AGENT_BATCH):
        bounded = max(1, min(int(limit or MAX_AGENT_BATCH), MAX_AGENT_BATCH))
        tasks = self.search(
            [
                ("agent_code", "=", "website_release"),
                ("state", "=", "completed"),
                ("publication_state", "=", "ready"),
            ],
            order="id",
            limit=bounded,
        )
        records = []
        for task in tasks:
            task.action_prepare_snapshot()
            if not task.ready_to_publish or not task.product_tmpl_id:
                task.write({"state": "blocked", "error_message": _("Readiness changed before release.")})
                continue
            product = task.product_tmpl_id.sudo()
            publication_fields = self._publication_fields()
            before_flags = {name: bool(product[name]) for name in publication_fields}
            records.append(
                {
                    "task_id": task.id,
                    "root_task_id": task.root_task_id.id,
                    "product_id": product.id,
                    "sku": task.normalized_sku,
                    "snapshot_sha256": task.snapshot_sha256,
                    "publication_fields_before": before_flags,
                    "invariants": self._product_invariants(product),
                    "worker_id": worker_id,
                }
            )
        return records

    @api.model
    def _publication_fields(self):
        details = self.env["product.template"].fields_get(
            ["is_published", "website_published"], attributes=["readonly"]
        )
        names = [
            name
            for name in ("is_published", "website_published")
            if name in details and not details[name].get("readonly")
        ]
        if not names:
            raise UserError(_("No writable website publication field is available."))
        return names

    @api.model
    def publish_prepared_tasks(self, records, worker_id, confirmation, reason):
        if confirmation != "catalog-agent-publication" or not (reason or "").strip():
            raise UserError(_("Publication requires the exact workflow confirmation and business reason."))
        if not records or len(records) > MAX_AGENT_BATCH:
            raise UserError(_("Publication batches must contain between 1 and 50 records."))
        results = []
        publication_fields = self._publication_fields()
        for prepared in records:
            task = self.browse(int(prepared["task_id"])).exists()
            if not task or task.agent_code != "website_release" or task.publication_state != "ready":
                raise UserError(_("Publication task is missing or no longer ready."))
            self.env.cr.execute("SELECT id FROM southern_catalog_agent_task WHERE id = %s FOR UPDATE NOWAIT", [task.id])
            task.action_prepare_snapshot()
            product = task.product_tmpl_id.sudo()
            if (
                not task.ready_to_publish
                or task.snapshot_sha256 != prepared.get("snapshot_sha256")
                or product.id != int(prepared.get("product_id") or 0)
                or task.normalized_sku != normalized_sparex_sku(prepared.get("sku"))
            ):
                raise UserError(_("Publication snapshot changed; create a fresh plan."))
            invariants = self._product_invariants(product)
            if invariants != prepared.get("invariants"):
                raise UserError(_("Price, cost, URL, image, or identity changed after planning."))
            before_flags = {name: bool(product[name]) for name in publication_fields}
            if before_flags != prepared.get("publication_fields_before"):
                raise UserError(_("Publication flags changed after planning."))
            product.write({name: True for name in publication_fields})
            product.invalidate_recordset(publication_fields)
            after_flags = {name: bool(product[name]) for name in publication_fields}
            if not all(after_flags.values()) or self._product_invariants(product) != invariants:
                product.write(before_flags)
                raise UserError(_("Publication verification failed and flags were restored."))
            public_path = product.website_url or f"/shop/product/{product.id}"
            task.write(
                {
                    "publication_state": "published",
                    "publication_fields_before_json": json.dumps(before_flags, sort_keys=True),
                    "publication_fields_after_json": json.dumps(after_flags, sort_keys=True),
                    "publication_public_url": public_path,
                    "published_at": fields.Datetime.now(),
                    "worker_id": worker_id,
                }
            )
            discovery_item = self._current_discovery_item(product, task.normalized_sku)
            if discovery_item:
                discovery_item._refresh_readiness()
            results.append(
                {"task_id": task.id, "product_id": product.id, "sku": task.normalized_sku, "public_path": public_path}
            )
        return results

    @api.model
    def confirm_publications(self, task_ids, verification_sha256):
        if not SHA256_PATTERN.fullmatch((verification_sha256 or "").casefold()):
            raise UserError(_("A valid public verification SHA-256 is required."))
        tasks = self.browse([int(value) for value in task_ids]).exists()
        if any(task.publication_state != "published" for task in tasks):
            raise UserError(_("Only newly published tasks can be confirmed."))
        tasks.write(
            {
                "publication_state": "verified",
                "publication_verification_sha256": verification_sha256.casefold(),
                "publication_verified_at": fields.Datetime.now(),
            }
        )
        return True

    @api.model
    def rollback_publications(self, task_ids, reason):
        if not (reason or "").strip():
            raise UserError(_("Rollback requires a reason."))
        tasks = self.browse([int(value) for value in task_ids]).exists()
        for task in tasks:
            if task.publication_state not in {"published", "failed"} or not task.product_tmpl_id:
                continue
            try:
                before = json.loads(task.publication_fields_before_json or "{}")
            except (TypeError, ValueError) as exc:
                raise UserError(_("Rollback snapshot is invalid.")) from exc
            allowed = set(self._publication_fields())
            values = {name: bool(value) for name, value in before.items() if name in allowed}
            if not values:
                raise UserError(_("Rollback snapshot contains no publication fields."))
            task.product_tmpl_id.sudo().write(values)
            task.write(
                {
                    "publication_state": "rolled_back",
                    "state": "failed",
                    "error_message": (reason or "")[:2000],
                    "finished_at": fields.Datetime.now(),
                }
            )
            discovery_item = self._current_discovery_item(
                task.product_tmpl_id.sudo(), task.normalized_sku
            )
            if discovery_item:
                discovery_item._refresh_readiness()
        return True

    def action_cancel(self):
        active = self.filtered(lambda task: task.state in {"queued", "claimed"})
        active.write({"state": "cancelled", "finished_at": fields.Datetime.now()})
        return True
