import hashlib
from datetime import timedelta
from urllib.parse import urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .catalog_agents import SHA256_PATTERN, exact_sparex_url, normalized_sparex_sku

SPAREX_DISCOVERY_HOSTS = {"us.sparex.com"}
MAX_DISCOVERY_PAGE_ITEMS = 100
MAX_DISCOVERY_CHECKPOINT_PAGES = 5


def _https_sparex_url(value):
    parsed = urlsplit((value or "").strip())
    return (
        parsed.scheme.casefold() == "https" and (parsed.hostname or "").casefold().rstrip(".") in SPAREX_DISCOVERY_HOSTS
    )


def _https_url(value):
    parsed = urlsplit((value or "").strip())
    return parsed.scheme.casefold() == "https" and bool(parsed.hostname)


def _sha256_text(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


class SouthernSparexDiscoveryRun(models.Model):
    _name = "southern.sparex.discovery.run"
    _description = "Sparex Catalog Discovery Run"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model metadata
    _order = "create_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    idempotency_key = fields.Char(required=True, copy=False, index=True)
    state = fields.Selection(
        [
            ("ready", "Ready"),
            ("running", "Running"),
            ("cooldown", "Cooldown"),
            ("completed", "Completed"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="ready",
        required=True,
        tracking=True,
        index=True,
    )
    discovery_agent_id = fields.Many2one("southern.catalog.agent", required=True, ondelete="restrict")
    match_agent_id = fields.Many2one("southern.catalog.agent", required=True, ondelete="restrict")
    seed_url = fields.Char(required=True, readonly=True, copy=False)
    seed_url_sha256 = fields.Char(required=True, readonly=True, copy=False)
    cursor_url = fields.Char(required=True, readonly=True, copy=False)
    cursor_url_sha256 = fields.Char(required=True, readonly=True, copy=False)
    parser_version = fields.Char(required=True, readonly=True)
    schema_version = fields.Char(default="1.0", required=True, readonly=True)
    plan_artifact_uri = fields.Char(required=True, readonly=True, copy=False)
    plan_sha256 = fields.Char(required=True, readonly=True, copy=False)
    throttle_seconds = fields.Float(default=3.0, required=True)
    max_pages_per_checkpoint = fields.Integer(default=1, required=True)
    max_items_per_page = fields.Integer(default=MAX_DISCOVERY_PAGE_ITEMS, required=True)
    page_count = fields.Integer(default=0, readonly=True)
    observed_count = fields.Integer(default=0, readonly=True)
    matched_count = fields.Integer(default=0, readonly=True)
    missing_count = fields.Integer(default=0, readonly=True)
    duplicate_count = fields.Integer(default=0, readonly=True)
    review_count = fields.Integer(default=0, readonly=True)
    lease_owner = fields.Char(readonly=True, copy=False, index=True)
    lease_expires_at = fields.Datetime(readonly=True, copy=False, index=True)
    last_request_at = fields.Datetime(readonly=True, copy=False)
    next_request_at = fields.Datetime(readonly=True, copy=False, index=True)
    cooldown_until = fields.Datetime(readonly=True, copy=False, index=True)
    error_code = fields.Char(readonly=True, copy=False, index=True)
    error_message = fields.Text(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    page_ids = fields.One2many("southern.sparex.discovery.page", "run_id")
    item_ids = fields.One2many("southern.sparex.discovery.item", "last_seen_run_id")

    _idempotency_company_unique = models.Constraint(
        "unique(idempotency_key, company_id)", "A Sparex discovery run already exists for this key and company."
    )

    @api.depends("idempotency_key", "state")
    def _compute_name(self):
        for run in self:
            run.name = f"{run.idempotency_key or _('New discovery')} / {run.state or 'ready'}"

    @api.constrains(
        "seed_url_sha256",
        "cursor_url_sha256",
        "plan_sha256",
        "throttle_seconds",
        "max_pages_per_checkpoint",
        "max_items_per_page",
    )
    def _check_contract(self):
        for run in self:
            for value in (run.seed_url_sha256, run.cursor_url_sha256, run.plan_sha256):
                if value and not SHA256_PATTERN.fullmatch(value.casefold()):
                    raise ValidationError(_("Sparex discovery hashes must be SHA-256 hexadecimal values."))
            if run.throttle_seconds < 3.0:
                raise ValidationError(_("Sparex discovery throttling cannot be less than 3 seconds."))
            if not 1 <= run.max_pages_per_checkpoint <= MAX_DISCOVERY_CHECKPOINT_PAGES:
                raise ValidationError(_("A discovery checkpoint must contain between 1 and 5 listing pages."))
            if not 1 <= run.max_items_per_page <= MAX_DISCOVERY_PAGE_ITEMS:
                raise ValidationError(_("A discovery listing page must contain between 1 and 100 observations."))

    @api.model
    def start_discovery_run(self, values):
        values = dict(values or {})
        key = (values.get("idempotency_key") or "").strip()
        seed_url = (values.get("seed_url") or "").strip()
        seed_sha = (values.get("seed_url_sha256") or "").casefold()
        plan_sha = (values.get("plan_sha256") or "").casefold()
        plan_uri = (values.get("plan_artifact_uri") or "").strip()
        if not key or not seed_url or not plan_uri:
            raise UserError(_("Discovery runs require an idempotency key, explicit seed URL, and archived plan."))
        if not _https_sparex_url(seed_url) or seed_sha != _sha256_text(seed_url):
            raise UserError(_("The explicit Sparex seed URL or its hash is invalid."))
        if not SHA256_PATTERN.fullmatch(plan_sha):
            raise UserError(_("The archived discovery plan requires a valid SHA-256."))
        existing = self.search([("idempotency_key", "=", key), ("company_id", "=", self.env.company.id)], limit=1)
        if existing:
            if existing.seed_url_sha256 != seed_sha or existing.plan_sha256 != plan_sha:
                raise UserError(_("The discovery run key is already bound to a different explicit plan."))
            return existing.read(self._worker_fields())[0]
        agents = self.env["southern.catalog.agent"]
        discovery_agent = agents.search(
            [("company_id", "=", self.env.company.id), ("code", "=", "sparex_discovery"), ("active", "=", True)],
            limit=1,
        )
        match_agent = agents.search(
            [("company_id", "=", self.env.company.id), ("code", "=", "odoo_match"), ("active", "=", True)],
            limit=1,
        )
        if not discovery_agent or not match_agent:
            raise UserError(_("The existing Sparex Discovery and Odoo Match agents must be active."))
        run = self.create(
            {
                "company_id": self.env.company.id,
                "idempotency_key": key,
                "discovery_agent_id": discovery_agent.id,
                "match_agent_id": match_agent.id,
                "seed_url": seed_url,
                "seed_url_sha256": seed_sha,
                "cursor_url": seed_url,
                "cursor_url_sha256": seed_sha,
                "parser_version": (values.get("parser_version") or "sparex-listing-v1").strip(),
                "schema_version": (values.get("schema_version") or "1.0").strip(),
                "plan_artifact_uri": plan_uri,
                "plan_sha256": plan_sha,
                "throttle_seconds": max(3.0, float(values.get("throttle_seconds") or 3.0)),
                "max_pages_per_checkpoint": max(
                    1, min(int(values.get("max_pages_per_checkpoint") or 1), MAX_DISCOVERY_CHECKPOINT_PAGES)
                ),
                "max_items_per_page": max(
                    1, min(int(values.get("max_items_per_page") or MAX_DISCOVERY_PAGE_ITEMS), MAX_DISCOVERY_PAGE_ITEMS)
                ),
            }
        )
        return run.read(self._worker_fields())[0]

    @api.model
    def _worker_fields(self):
        return [
            "id",
            "idempotency_key",
            "state",
            "seed_url",
            "seed_url_sha256",
            "cursor_url",
            "cursor_url_sha256",
            "parser_version",
            "schema_version",
            "plan_artifact_uri",
            "plan_sha256",
            "throttle_seconds",
            "max_pages_per_checkpoint",
            "max_items_per_page",
            "page_count",
            "observed_count",
            "matched_count",
            "missing_count",
            "duplicate_count",
            "review_count",
            "cooldown_until",
        ]

    @api.model
    def claim_discovery_checkpoint(self, run_id, worker_id, lease_seconds=180):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The requested discovery run does not exist in the active company."))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_run WHERE id = %s FOR UPDATE NOWAIT", [run.id])
        run.invalidate_recordset()
        now = fields.Datetime.now()
        if run.state in {"completed", "failed", "cancelled"}:
            return {"claimed": False, "state": run.state}
        if run.state == "cooldown" and run.cooldown_until and run.cooldown_until > now:
            return {"claimed": False, "state": run.state, "cooldown_until": run.cooldown_until}
        if run.lease_owner and run.lease_expires_at and run.lease_expires_at > now and run.lease_owner != worker_id:
            return {"claimed": False, "state": "busy"}
        run.write(
            {
                "state": "running",
                "lease_owner": (worker_id or "external-worker")[:255],
                "lease_expires_at": now + timedelta(seconds=max(60, min(int(lease_seconds or 180), 900))),
                "cooldown_until": False,
                "error_code": False,
                "error_message": False,
            }
        )
        return {"claimed": True, **run.read(self._worker_fields())[0]}

    @api.model
    def record_discovery_page(self, run_id, worker_id, page):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_run WHERE id = %s FOR UPDATE NOWAIT", [run.id])
        run.invalidate_recordset()
        if run.state != "running" or run.lease_owner != worker_id:
            raise UserError(_("The worker does not own the active discovery lease."))
        page = dict(page or {})
        page_url = (page.get("page_url") or "").strip()
        page_sha = (page.get("page_sha256") or "").casefold()
        artifact_sha = (page.get("artifact_sha256") or "").casefold()
        artifact_uri = (page.get("artifact_uri") or "").strip()
        items = list(page.get("items") or [])
        if page_url != run.cursor_url or not _https_sparex_url(page_url):
            raise UserError(_("The listing page does not match the explicit discovery cursor."))
        if not SHA256_PATTERN.fullmatch(page_sha) or not SHA256_PATTERN.fullmatch(artifact_sha) or not artifact_uri:
            raise UserError(_("The listing checkpoint requires checksum-verified evidence."))
        if len(items) > run.max_items_per_page:
            raise UserError(_("The listing page exceeds the bounded discovery item limit."))
        page_url_sha = _sha256_text(page_url)
        existing_page = self.env["southern.sparex.discovery.page"].search(
            [("run_id", "=", run.id), ("page_url_sha256", "=", page_url_sha)], limit=1
        )
        if existing_page:
            run.write({"lease_owner": False, "lease_expires_at": False})
            return {"idempotent": True, "page_id": existing_page.id, "state": run.state}

        now = fields.Datetime.now()
        page_record = self.env["southern.sparex.discovery.page"].create(
            {
                "run_id": run.id,
                "company_id": run.company_id.id,
                "page_number": run.page_count + 1,
                "page_url_sha256": page_url_sha,
                "page_sha256": page_sha,
                "artifact_uri": artifact_uri,
                "artifact_sha256": artifact_sha,
                "retrieved_at": now,
            }
        )
        seen_skus = set()
        page_counts = {"matched": 0, "missing": 0, "duplicate": 0, "review": 0}
        Item = self.env["southern.sparex.discovery.item"]
        for observation in items:
            raw_sku = (observation.get("sku") or "").strip()
            normalized = normalized_sparex_sku(raw_sku)
            source_url = (observation.get("source_url") or "").strip()
            image_url = (observation.get("image_url") or "").strip()
            source_state = (observation.get("source_state") or "verified").strip()
            if not normalized or normalized in seen_skus:
                raise UserError(_("Each listing observation must contain one unique exact Sparex SKU."))
            seen_skus.add(normalized)
            if not exact_sparex_url(source_url, normalized):
                raise UserError(_("A listing observation contains a non-exact Sparex product link."))
            if image_url and not _https_url(image_url):
                raise UserError(_("A listing observation contains an invalid image URL."))
            if source_state not in {"verified", "missing_image", "ambiguous"}:
                raise UserError(_("A listing observation contains an invalid source state."))

            digits = normalized.split(".", 1)[1]
            candidates = (
                self.env["product.template"]
                .with_context(active_test=False)
                .search(["|", ("default_code", "ilike", f"S.{digits}"), ("default_code", "ilike", f"S{digits}")])
            )
            exact = candidates.filtered(
                lambda product, target=normalized: normalized_sparex_sku(product.default_code) == target
            )
            if len(exact) > 1:
                match_state = "duplicate"
                matched_product = self.env["product.template"]
            elif exact:
                matched_product = exact[0]
                match_state = "matched_active" if matched_product.active else "matched_archived"
            else:
                matched_product = self.env["product.template"]
                match_state = "missing"
            positive_supplier = self.env["product.supplierinfo"]
            if matched_product:
                positive_supplier = (
                    self.env["product.supplierinfo"]
                    .sudo()
                    .search(
                        [
                            ("product_tmpl_id", "=", matched_product.id),
                            ("partner_id.name", "=ilike", "Sparex"),
                            ("price", ">", 0),
                        ],
                        limit=1,
                    )
                )
            queue_state = "verified" if source_state == "verified" and image_url else "review"
            has_cost = bool(positive_supplier)
            has_sales_price = bool(matched_product and matched_product.list_price > 0)
            has_exact_url = exact_sparex_url(source_url, normalized)
            has_image = bool(image_url)
            currently_published = bool(matched_product and matched_product.website_published)
            product_has_exact_url = bool(
                matched_product and exact_sparex_url(matched_product.southern_source_url, normalized)
            )
            product_has_image = bool(matched_product and matched_product.image_1920)
            source_enrichment_candidate = bool(
                matched_product
                and matched_product.active
                and not currently_published
                and has_cost
                and has_sales_price
                and has_exact_url
                and has_image
                and queue_state == "verified"
            )
            publication_candidate = bool(source_enrichment_candidate and product_has_exact_url and product_has_image)
            item = Item.search([("company_id", "=", run.company_id.id), ("normalized_sku", "=", normalized)], limit=1)
            values = {
                "raw_sku": raw_sku,
                "source_url": source_url,
                "source_url_sha256": _sha256_text(source_url),
                "image_url": image_url or False,
                "image_url_sha256": _sha256_text(image_url) if image_url else False,
                "source_state": source_state,
                "state": queue_state,
                "odoo_match_state": match_state,
                "matched_product_id": matched_product.id if matched_product else False,
                "duplicate_product_ids": [(6, 0, exact.ids if len(exact) > 1 else [])],
                "has_positive_supplier_cost": has_cost,
                "has_positive_sales_price": has_sales_price,
                "has_exact_sparex_url": has_exact_url,
                "has_image": has_image,
                "product_has_exact_sparex_url": product_has_exact_url,
                "product_has_image": product_has_image,
                "currently_published": currently_published,
                "source_enrichment_candidate": source_enrichment_candidate,
                "publication_candidate": publication_candidate,
                "last_seen_run_id": run.id,
                "last_seen_page_id": page_record.id,
                "last_seen_at": now,
                "source_artifact_uri": artifact_uri,
                "source_artifact_sha256": artifact_sha,
            }
            if item:
                values["observation_count"] = item.observation_count + 1
                item.write(values)
            else:
                values.update(
                    {
                        "company_id": run.company_id.id,
                        "normalized_sku": normalized,
                        "first_seen_run_id": run.id,
                        "first_seen_at": now,
                    }
                )
                item = Item.create(values)
            if queue_state == "review":
                page_counts["review"] += 1
            if match_state.startswith("matched_"):
                page_counts["matched"] += 1
            elif match_state == "missing":
                page_counts["missing"] += 1
            elif match_state == "duplicate":
                page_counts["duplicate"] += 1

        page_record.write(
            {
                "item_count": len(items),
                "matched_count": page_counts["matched"],
                "missing_count": page_counts["missing"],
                "duplicate_count": page_counts["duplicate"],
                "review_count": page_counts["review"],
            }
        )
        next_url = (page.get("next_url") or "").strip()
        if next_url and not _https_sparex_url(next_url):
            raise UserError(_("The next listing cursor is not an HTTPS Sparex URL."))
        completed = not next_url
        run.write(
            {
                "state": "completed" if completed else "ready",
                "cursor_url": next_url or page_url,
                "cursor_url_sha256": _sha256_text(next_url or page_url),
                "page_count": run.page_count + 1,
                "observed_count": run.observed_count + len(items),
                "matched_count": run.matched_count + page_counts["matched"],
                "missing_count": run.missing_count + page_counts["missing"],
                "duplicate_count": run.duplicate_count + page_counts["duplicate"],
                "review_count": run.review_count + page_counts["review"],
                "last_request_at": now,
                "next_request_at": False if completed else now + timedelta(seconds=run.throttle_seconds),
                "completed_at": now if completed else False,
                "lease_owner": False,
                "lease_expires_at": False,
            }
        )
        return {
            "idempotent": False,
            "page_id": page_record.id,
            "state": run.state,
            "next_cursor_sha256": run.cursor_url_sha256,
            "counts": page_counts,
            "observed": len(items),
        }

    @api.model
    def record_discovery_failure(self, run_id, worker_id, error_code, cooldown=False):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        if run.lease_owner != worker_id:
            raise UserError(_("The worker does not own the active discovery lease."))
        now = fields.Datetime.now()
        run.write(
            {
                "state": "cooldown" if cooldown else "ready",
                "cooldown_until": now + timedelta(minutes=60) if cooldown else False,
                "error_code": (error_code or "checkpoint_failed")[:255],
                "error_message": _("Discovery checkpoint failed; the explicit cursor was preserved."),
                "lease_owner": False,
                "lease_expires_at": False,
            }
        )
        return True


class SouthernSparexDiscoveryPage(models.Model):
    _name = "southern.sparex.discovery.page"
    _description = "Sparex Catalog Discovery Page"
    _order = "run_id desc, page_number"

    run_id = fields.Many2one("southern.sparex.discovery.run", required=True, ondelete="restrict", index=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    page_number = fields.Integer(required=True)
    page_url_sha256 = fields.Char(required=True, readonly=True, copy=False)
    page_sha256 = fields.Char(required=True, readonly=True, copy=False)
    artifact_uri = fields.Char(required=True, readonly=True, copy=False)
    artifact_sha256 = fields.Char(required=True, readonly=True, copy=False)
    retrieved_at = fields.Datetime(required=True, readonly=True)
    item_count = fields.Integer(default=0, readonly=True)
    matched_count = fields.Integer(default=0, readonly=True)
    missing_count = fields.Integer(default=0, readonly=True)
    duplicate_count = fields.Integer(default=0, readonly=True)
    review_count = fields.Integer(default=0, readonly=True)

    _page_run_unique = models.Constraint(
        "unique(run_id, page_url_sha256)", "This listing cursor was already recorded for the discovery run."
    )

    @api.constrains("page_url_sha256", "page_sha256", "artifact_sha256")
    def _check_hashes(self):
        for page in self:
            if any(
                not SHA256_PATTERN.fullmatch((value or "").casefold())
                for value in (
                    page.page_url_sha256,
                    page.page_sha256,
                    page.artifact_sha256,
                )
            ):
                raise ValidationError(_("Discovery page hashes must be SHA-256 hexadecimal values."))


class SouthernSparexDiscoveryItem(models.Model):
    _name = "southern.sparex.discovery.item"
    _description = "Sparex Catalog Discovery Queue"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model metadata
    _order = "odoo_match_state, normalized_sku, id"

    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    normalized_sku = fields.Char(required=True, readonly=True, index=True, tracking=True)
    raw_sku = fields.Char(required=True, readonly=True)
    state = fields.Selection(
        [("verified", "Source Verified"), ("review", "Source Review")],
        default="review",
        required=True,
        readonly=True,
        index=True,
    )
    source_state = fields.Selection(
        [("verified", "Exact Link and Image"), ("missing_image", "Missing Image"), ("ambiguous", "Ambiguous Listing")],
        required=True,
        readonly=True,
        index=True,
    )
    source_url = fields.Char(required=True, readonly=True, copy=False)
    source_url_sha256 = fields.Char(required=True, readonly=True, copy=False)
    image_url = fields.Char(readonly=True, copy=False)
    image_url_sha256 = fields.Char(readonly=True, copy=False)
    odoo_match_state = fields.Selection(
        [
            ("matched_active", "Existing Active Product"),
            ("matched_archived", "Existing Archived Product"),
            ("missing", "Missing in Odoo"),
            ("duplicate", "Duplicate Odoo SKU"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    matched_product_id = fields.Many2one("product.template", readonly=True, index=True, ondelete="set null")
    duplicate_product_ids = fields.Many2many("product.template", string="Duplicate Products", readonly=True)
    has_positive_supplier_cost = fields.Boolean(readonly=True, index=True)
    has_positive_sales_price = fields.Boolean(readonly=True, index=True)
    has_exact_sparex_url = fields.Boolean(readonly=True, index=True)
    has_image = fields.Boolean(readonly=True, index=True)
    product_has_exact_sparex_url = fields.Boolean(readonly=True, index=True)
    product_has_image = fields.Boolean(readonly=True, index=True)
    currently_published = fields.Boolean(readonly=True, index=True)
    source_enrichment_candidate = fields.Boolean(readonly=True, index=True)
    publication_candidate = fields.Boolean(readonly=True, index=True)
    creation_state = fields.Selection(
        [("not_authorized", "Product Creation Not Authorized")],
        default="not_authorized",
        required=True,
        readonly=True,
    )
    first_seen_run_id = fields.Many2one(
        "southern.sparex.discovery.run", required=True, readonly=True, ondelete="restrict"
    )
    last_seen_run_id = fields.Many2one(
        "southern.sparex.discovery.run", required=True, readonly=True, ondelete="restrict"
    )
    last_seen_page_id = fields.Many2one(
        "southern.sparex.discovery.page", required=True, readonly=True, ondelete="restrict"
    )
    first_seen_at = fields.Datetime(required=True, readonly=True)
    last_seen_at = fields.Datetime(required=True, readonly=True)
    observation_count = fields.Integer(default=1, required=True, readonly=True)
    source_artifact_uri = fields.Char(required=True, readonly=True, copy=False)
    source_artifact_sha256 = fields.Char(required=True, readonly=True, copy=False)

    _sku_company_unique = models.Constraint(
        "unique(normalized_sku, company_id)", "Each Sparex SKU can appear only once in the company discovery queue."
    )

    @api.constrains(
        "normalized_sku", "source_url", "source_url_sha256", "image_url", "image_url_sha256", "source_artifact_sha256"
    )
    def _check_contract(self):
        for item in self:
            if normalized_sparex_sku(item.normalized_sku) != item.normalized_sku:
                raise ValidationError(_("Discovery items require a normalized exact Sparex SKU."))
            if not exact_sparex_url(item.source_url, item.normalized_sku):
                raise ValidationError(_("Discovery items require an exact same-SKU Sparex URL."))
            if item.source_url_sha256 != _sha256_text(item.source_url):
                raise ValidationError(_("The discovery source URL hash does not match."))
            if item.image_url and (
                not _https_url(item.image_url) or item.image_url_sha256 != _sha256_text(item.image_url)
            ):
                raise ValidationError(_("The discovery image URL or hash is invalid."))
            if any(
                value and not SHA256_PATTERN.fullmatch(value.casefold())
                for value in (item.source_url_sha256, item.image_url_sha256, item.source_artifact_sha256)
            ):
                raise ValidationError(_("Discovery evidence hashes must be SHA-256 hexadecimal values."))
