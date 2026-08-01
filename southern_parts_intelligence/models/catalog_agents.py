import hashlib
import json
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
MAX_AGENT_BATCH = 5
SPAREX_HOSTS = {"us.sparex.com"}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SPAREX_SKU_PATTERN = re.compile(r"^S[.\s-]?0*(\d+)$", re.IGNORECASE)


def normalized_sparex_sku(value):
    match = SPAREX_SKU_PATTERN.fullmatch((value or "").strip())
    if not match:
        return ""
    return "S.%s" % int(match.group(1))


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


class SouthernCatalogAgent(models.Model):
    _name = "southern.catalog.agent"
    _description = "Southern Catalog Agent"
    _inherit = ["mail.thread"]
    _order = "sequence, id"

    name = fields.Char(required=True, tracking=True)
    code = fields.Selection(CATALOG_AGENT_CODES, required=True, index=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    model_name = fields.Char(
        default="gpt-5.6",
        required=True,
        help="OpenAI model used by the external Agents SDK worker. API credentials are never stored in Odoo.",
    )
    instructions = fields.Text(required=True)
    batch_size = fields.Integer(default=MAX_AGENT_BATCH, required=True)
    throttle_seconds = fields.Float(default=3.0, required=True)
    ai_enabled = fields.Boolean(
        default=False,
        tracking=True,
        help="Enables the external OpenAI worker for this profile. This does not enable product writes.",
    )
    internal_cron_enabled = fields.Boolean(
        default=False,
        tracking=True,
        help="Catalog agents install with internal scheduling disabled.",
    )
    task_ids = fields.One2many("southern.catalog.agent.task", "agent_id")
    task_count = fields.Integer(compute="_compute_task_count")

    _code_company_unique = models.Constraint(
        "unique(code, company_id)",
        "Each company can have only one profile for each catalog agent.",
    )

    @api.depends("task_ids")
    def _compute_task_count(self):
        for agent in self:
            agent.task_count = len(agent.task_ids)

    @api.constrains("batch_size", "throttle_seconds")
    def _check_limits(self):
        for agent in self:
            if not 1 <= agent.batch_size <= MAX_AGENT_BATCH:
                raise ValidationError(_("Catalog agent batches must contain between 1 and 5 records."))
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
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, create_date, id"

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    agent_id = fields.Many2one(
        "southern.catalog.agent",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    agent_code = fields.Selection(related="agent_id.code", store=True, index=True)
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
    idempotency_key = fields.Char(
        required=True,
        copy=False,
        index=True,
        default=lambda self: uuid.uuid4().hex,
    )
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

    _idempotency_unique = models.Constraint(
        "unique(idempotency_key)",
        "A catalog agent task already exists for this idempotency key.",
    )

    @api.depends("external_sku", "agent_id")
    def _compute_name(self):
        for task in self:
            task.name = "%s / %s" % (task.external_sku or _("No SKU"), task.agent_id.name or _("No Agent"))

    @api.constrains(
        "dealer_cost_url_sha256",
        "retail_price_url_sha256",
        "source_artifact_sha256",
        "result_sha256",
    )
    def _check_hashes(self):
        for task in self:
            for value in (
                task.dealer_cost_url_sha256,
                task.retail_price_url_sha256,
                task.source_artifact_sha256,
                task.result_sha256,
            ):
                if value and not SHA256_PATTERN.fullmatch(value.casefold()):
                    raise ValidationError(_("Catalog agent evidence hashes must be SHA-256 hexadecimal values."))

    def _find_exact_product(self):
        self.ensure_one()
        normalized = normalized_sparex_sku(self.external_sku)
        if not normalized:
            return normalized, self.env["product.template"]
        digits = normalized.split(".", 1)[1]
        candidates = self.env["product.template"].with_context(active_test=False).search(
            ["|", ("default_code", "ilike", "S.%s" % digits), ("default_code", "ilike", "S%s" % digits)]
        )
        exact = candidates.filtered(lambda product: normalized_sparex_sku(product.default_code) == normalized)
        return normalized, exact

    def action_prepare_snapshot(self):
        SupplierInfo = self.env["product.supplierinfo"].sudo()
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

            has_cost = False
            has_sales_price = False
            has_url = False
            has_image = False
            is_hidden = False
            if product:
                supplierinfo = SupplierInfo.search(
                    [
                        ("product_tmpl_id", "=", product.id),
                        ("partner_id.name", "=ilike", "Sparex"),
                        ("price", ">", 0),
                    ],
                    limit=1,
                )
                has_cost = bool(supplierinfo)
                has_sales_price = product.list_price > 0
                has_url = exact_sparex_url(product.southern_source_url, normalized)
                has_image = bool(product.image_1920)
                is_hidden = not bool(product.website_published)
                if not product.active:
                    blockers.append("product_archived")
                if not is_hidden:
                    blockers.append("already_published")
                if not has_cost:
                    blockers.append("missing_positive_sparex_cost")
                if not has_sales_price:
                    blockers.append("missing_positive_sales_price")
                if not has_url:
                    blockers.append("missing_exact_sparex_url")
                if not has_image:
                    blockers.append("missing_image")

            ready = bool(product and product.active and is_hidden and has_cost and has_sales_price and has_url and has_image)
            snapshot = {
                "schema_version": "1.0",
                "agent": task.agent_code,
                "task_id": task.id,
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
        task.action_prepare_snapshot()
        return task.id

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
            SELECT id
              FROM southern_catalog_agent_task
             WHERE company_id = %s
               AND agent_id = %s
               AND state = 'queued'
             ORDER BY priority DESC, create_date, id
             FOR UPDATE SKIP LOCKED
             LIMIT %s
            """,
            [self.env.company.id, agent.id, bounded_limit],
        )
        task_ids = [row[0] for row in self.env.cr.fetchall()]
        tasks = self.browse(task_ids)
        tasks.write({"state": "claimed", "worker_id": worker_id, "claimed_at": fields.Datetime.now()})
        return tasks.read(["id", "agent_code", "external_sku", "input_json", "idempotency_key"])

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
            json.loads(canonical_output)
        except (TypeError, ValueError) as exc:
            raise UserError(_("Catalog agent results must be valid JSON.")) from exc
        task.write(
            {
                "state": state,
                "output_json": canonical_output,
                "result_sha256": actual_sha,
                "finished_at": fields.Datetime.now(),
                "error_message": False if state != "failed" else _("The external catalog agent reported failure."),
            }
        )
        return task.id

    def action_cancel(self):
        active = self.filtered(lambda task: task.state in {"queued", "claimed"})
        active.write({"state": "cancelled", "finished_at": fields.Datetime.now()})
        return True
