import base64
import hashlib
import html
import json
import math
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .catalog_agents import (
    CUSTOMER_COPY_CONTAMINATION_MARKERS,
    SHA256_PATTERN,
    customer_description_ready,
    exact_dealer_cost_evidence_ready,
    exact_sparex_url,
    normalized_sparex_sku,
    pricing_basis_blockers,
    sales_price_blocker,
)

SPAREX_DISCOVERY_HOSTS = {"us.sparex.com"}
MAX_DISCOVERY_PAGE_ITEMS = 100
MAX_DISCOVERY_CHECKPOINT_PAGES = 10
MAX_DISCOVERY_LINKS_PER_PAGE = 5_000
MAX_DISCOVERY_TOTAL_PAGES = 10_000_000
MAX_DISCOVERY_REPAIR_BATCH = 500
MAX_SOURCE_LINK_BATCH = 50
MAX_COST_RECOVERY_BATCH = 50
MAX_COST_RECOVERY_ATTEMPTS = 5
MAX_PRODUCT_CREATION_BATCH = 100
SOURCE_LINK_CONFIRMATION = "sparex-discovery-source-link"
DESCRIPTION_REPAIR_CONFIRMATION = "sparex-listing-description-repair"
COST_RECOVERY_CONFIRMATION = "sparex-dealer-cost-recovery"
PRODUCT_CREATION_CONFIRMATION = "sparex-page-driven-draft-creation"
DEFAULT_COST_PLUS_MARGIN_PERCENT = 35.0
DETAIL_TITLE_PLACEHOLDERS = {
    "access denied",
    "error",
    "home",
    "login",
    "page not found",
    "product",
    "search results",
    "sign in",
}
PRODUCT_DETAIL_PATH = re.compile(r"(?:^|[-/])\d+\.html$", re.IGNORECASE)
LISTING_PATH_DENY_PREFIXES = (
    "/about",
    "/account",
    "/catalogue",
    "/checkout",
    "/contact",
    "/cookie",
    "/customer",
    "/help",
    "/login",
    "/media",
    "/privacy",
    "/sales",
    "/search",
    "/wishlist",
)


def _https_sparex_url(value):
    parsed = urlsplit((value or "").strip())
    return (
        parsed.scheme.casefold() == "https" and (parsed.hostname or "").casefold().rstrip(".") in SPAREX_DISCOVERY_HOSTS
    )


def _https_url(value):
    parsed = urlsplit((value or "").strip())
    return parsed.scheme.casefold() == "https" and bool(parsed.hostname)


def _sparex_listing_url(value):
    parsed = urlsplit((value or "").strip())
    path = parsed.path.rstrip("/") or "/"
    return (
        _https_sparex_url(value)
        and not PRODUCT_DETAIL_PATH.search(path)
        and not path.casefold().startswith(LISTING_PATH_DENY_PREFIXES)
    )


def _sha256_text(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _verified_detail_title(value):
    title = " ".join(str(value or "").split()).strip()
    folded = re.sub(r"[^a-z0-9]+", " ", title.casefold()).strip()
    if (
        len(title) < 3
        or len(title) > 255
        or not re.search(r"[A-Za-z]", title)
        or bool(re.fullmatch(r"S[.\s-]*\d+", title, flags=re.IGNORECASE))
        or "<" in title
        or ">" in title
        or any(marker in title.casefold() for marker in CUSTOMER_COPY_CONTAMINATION_MARKERS)
        or folded in DETAIL_TITLE_PLACEHOLDERS
    ):
        return ""
    return title


def _frontier_priority(value):
    """Prefer product pagination before broad category expansion."""
    parsed = urlsplit((value or "").strip())
    query = parse_qs(parsed.query)
    if {"p", "page"} & set(query):
        return (0, parsed.path.count("/"), value)
    if parsed.path.casefold().endswith(".html"):
        return (1, parsed.path.count("/"), value)
    return (2, parsed.path.count("/"), value)


def _primary_publication_blocker(
    *,
    reconciliation_state,
    item_state,
    source_state,
    match_state,
    product_active,
    currently_published,
    has_cost,
    has_sales_price,
    product_has_exact_url,
    product_has_image,
    product_has_category,
    product_has_description,
):
    if reconciliation_state != "current":
        return "stale"
    if item_state != "verified" or source_state != "verified":
        return "source_review"
    if match_state == "missing":
        return "missing_odoo"
    if match_state == "duplicate":
        return "duplicate_odoo"
    if match_state == "matched_archived" or not product_active:
        return "archived"
    if not has_cost:
        return "missing_cost"
    if currently_published:
        return "already_published"
    if not has_sales_price:
        return "missing_sales_price"
    if not product_has_exact_url:
        return "missing_product_url"
    if not product_has_image:
        return "missing_product_image"
    if not product_has_category:
        return "missing_website_category"
    if not product_has_description:
        return "missing_customer_description"
    return "ready"


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
    cursor_kind = fields.Selection(
        [("frontier", "Catalog Frontier"), ("repair", "Targeted Repair")],
        default="frontier",
        required=True,
        readonly=True,
        copy=False,
    )
    parser_version = fields.Char(required=True, readonly=True)
    schema_version = fields.Char(default="1.0", required=True, readonly=True)
    plan_artifact_uri = fields.Char(required=True, readonly=True, copy=False)
    plan_sha256 = fields.Char(required=True, readonly=True, copy=False)
    throttle_seconds = fields.Float(default=3.0, required=True)
    max_pages_per_checkpoint = fields.Integer(default=1, required=True)
    max_items_per_page = fields.Integer(default=MAX_DISCOVERY_PAGE_ITEMS, required=True)
    max_pages_total = fields.Integer(default=MAX_DISCOVERY_TOTAL_PAGES, required=True)
    frontier_urls = fields.Text(readonly=True, copy=False)
    visited_url_sha256s = fields.Text(readonly=True, copy=False)
    frontier_migrated = fields.Boolean(default=False, readonly=True, copy=False)
    queued_url_count = fields.Integer(default=0, readonly=True)
    visited_url_count = fields.Integer(default=0, readonly=True)
    repair_queued_url_count = fields.Integer(default=0, readonly=True)
    repair_visited_url_count = fields.Integer(default=0, readonly=True)
    page_count = fields.Integer(default=0, readonly=True)
    observed_count = fields.Integer(default=0, readonly=True)
    matched_count = fields.Integer(default=0, readonly=True)
    missing_count = fields.Integer(default=0, readonly=True)
    duplicate_count = fields.Integer(default=0, readonly=True)
    review_count = fields.Integer(default=0, readonly=True)
    corrected_count = fields.Integer(default=0, readonly=True)
    stale_count = fields.Integer(default=0, readonly=True)
    product_page_count = fields.Integer(default=0, readonly=True)
    empty_page_count = fields.Integer(default=0, readonly=True)
    last_page_item_count = fields.Integer(default=0, readonly=True)
    average_items_per_product_page = fields.Float(compute="_compute_progress")
    current_item_count = fields.Integer(compute="_compute_dashboard_counts")
    missing_product_count = fields.Integer(compute="_compute_dashboard_counts")
    publication_candidate_count = fields.Integer(compute="_compute_dashboard_counts")
    source_repair_candidate_count = fields.Integer(compute="_compute_dashboard_counts")
    source_review_item_count = fields.Integer(compute="_compute_dashboard_counts")
    blocked_item_count = fields.Integer(compute="_compute_dashboard_counts")
    published_product_count = fields.Integer(compute="_compute_dashboard_counts")
    readiness_refreshed_count = fields.Integer(compute="_compute_dashboard_counts")
    cost_recovery_queued_count = fields.Integer(compute="_compute_dashboard_counts")
    cost_recovery_retry_count = fields.Integer(compute="_compute_dashboard_counts")
    cost_recovery_manual_count = fields.Integer(compute="_compute_dashboard_counts")
    missing_cost_blocker_count = fields.Integer(compute="_compute_dashboard_counts")
    missing_sales_price_blocker_count = fields.Integer(compute="_compute_dashboard_counts")
    missing_url_blocker_count = fields.Integer(compute="_compute_dashboard_counts")
    missing_image_blocker_count = fields.Integer(compute="_compute_dashboard_counts")
    progress_percent = fields.Float(compute="_compute_progress")
    estimated_checkpoints_remaining = fields.Integer(compute="_compute_progress")
    estimated_minutes_remaining = fields.Integer(compute="_compute_progress")
    reconciliation_state = fields.Selection(
        [("pending", "Pending"), ("in_progress", "In Progress"), ("completed", "Completed")],
        default="pending",
        required=True,
        readonly=True,
        index=True,
    )
    reconciliation_started_at = fields.Datetime(readonly=True)
    reconciliation_completed_at = fields.Datetime(readonly=True)
    recovery_state = fields.Selection(
        [("healthy", "Healthy"), ("retrying", "Retrying"), ("cooldown", "Portal Cooldown")],
        default="healthy",
        required=True,
        readonly=True,
        index=True,
    )
    consecutive_failure_count = fields.Integer(default=0, readonly=True)
    recovered_lease_count = fields.Integer(default=0, readonly=True)
    last_success_at = fields.Datetime(readonly=True)
    last_failure_at = fields.Datetime(readonly=True)
    lease_owner = fields.Char(readonly=True, copy=False, index=True)
    lease_expires_at = fields.Datetime(readonly=True, copy=False, index=True)
    last_request_at = fields.Datetime(readonly=True, copy=False)
    next_request_at = fields.Datetime(readonly=True, copy=False, index=True)
    cooldown_until = fields.Datetime(readonly=True, copy=False, index=True)
    error_code = fields.Char(readonly=True, copy=False, index=True)
    error_message = fields.Text(readonly=True, copy=False)
    completed_at = fields.Datetime(readonly=True, copy=False)
    page_ids = fields.One2many("southern.sparex.discovery.page", "run_id")
    url_ids = fields.One2many("southern.sparex.discovery.url", "run_id")
    item_ids = fields.One2many("southern.sparex.discovery.item", "last_seen_run_id")

    _idempotency_company_unique = models.Constraint(
        "unique(idempotency_key, company_id)", "A Sparex discovery run already exists for this key and company."
    )

    @api.depends("idempotency_key", "state")
    def _compute_name(self):
        for run in self:
            run.name = f"{run.idempotency_key or _('New discovery')} / {run.state or 'ready'}"

    @api.depends(
        "page_count",
        "queued_url_count",
        "max_pages_per_checkpoint",
        "observed_count",
        "product_page_count",
    )
    def _compute_progress(self):
        for run in self:
            total = run.page_count + run.queued_url_count
            run.progress_percent = (
                (100.0 * run.page_count / total) if total else (100.0 if run.state == "completed" else 0.0)
            )
            capacity = max(1, run.max_pages_per_checkpoint)
            checkpoints = (run.queued_url_count + capacity - 1) // capacity
            run.estimated_checkpoints_remaining = checkpoints
            run.estimated_minutes_remaining = checkpoints * 2
            run.average_items_per_product_page = (
                run.observed_count / run.product_page_count if run.product_page_count else 0.0
            )

    @api.depends(
        "item_ids.reconciliation_state",
        "item_ids.odoo_match_state",
        "item_ids.publication_candidate",
        "item_ids.source_enrichment_candidate",
        "item_ids.primary_blocker",
        "item_ids.cost_recovery_state",
        "item_ids.readiness_refreshed_at",
        "item_ids.matched_product_id.website_published",
    )
    def _compute_dashboard_counts(self):
        Item = self.env["southern.sparex.discovery.item"]
        for run in self:
            current_domain = [("last_seen_run_id", "=", run.id), ("reconciliation_state", "=", "current")]
            run.current_item_count = Item.search_count(current_domain)
            run.missing_product_count = Item.search_count(current_domain + [("odoo_match_state", "=", "missing")])
            run.publication_candidate_count = Item.search_count(current_domain + [("publication_candidate", "=", True)])
            run.source_repair_candidate_count = Item.search_count(
                current_domain + [("source_enrichment_candidate", "=", True)]
            )
            run.source_review_item_count = Item.search_count(current_domain + [("state", "=", "review")])
            blocked_domain = current_domain + [
                ("odoo_match_state", "=", "matched_active"),
                ("matched_product_id.website_published", "=", False),
                ("publication_candidate", "=", False),
            ]
            run.blocked_item_count = Item.search_count(blocked_domain)
            run.published_product_count = Item.search_count(
                current_domain + [("matched_product_id.website_published", "=", True)]
            )
            run.readiness_refreshed_count = Item.search_count(
                current_domain + [("readiness_refreshed_at", "!=", False)]
            )
            run.cost_recovery_queued_count = Item.search_count(
                current_domain + [("cost_recovery_state", "=", "queued")]
            )
            run.cost_recovery_retry_count = Item.search_count(
                current_domain + [("cost_recovery_state", "=", "retry_wait")]
            )
            run.cost_recovery_manual_count = Item.search_count(
                current_domain + [("cost_recovery_state", "=", "manual_review")]
            )
            run.missing_cost_blocker_count = Item.search_count(
                current_domain + [("primary_blocker", "=", "missing_cost")]
            )
            run.missing_sales_price_blocker_count = Item.search_count(
                current_domain + [("primary_blocker", "=", "missing_sales_price")]
            )
            run.missing_url_blocker_count = Item.search_count(
                current_domain + [("primary_blocker", "=", "missing_product_url")]
            )
            run.missing_image_blocker_count = Item.search_count(
                current_domain + [("primary_blocker", "=", "missing_product_image")]
            )

    @api.constrains(
        "seed_url_sha256",
        "cursor_url_sha256",
        "plan_sha256",
        "throttle_seconds",
        "max_pages_per_checkpoint",
        "max_items_per_page",
        "max_pages_total",
    )
    def _check_contract(self):
        for run in self:
            for value in (run.seed_url_sha256, run.cursor_url_sha256, run.plan_sha256):
                if value and not SHA256_PATTERN.fullmatch(value.casefold()):
                    raise ValidationError(_("Sparex discovery hashes must be SHA-256 hexadecimal values."))
            if run.throttle_seconds < 3.0:
                raise ValidationError(_("Sparex discovery throttling cannot be less than 3 seconds."))
            if not 1 <= run.max_pages_per_checkpoint <= MAX_DISCOVERY_CHECKPOINT_PAGES:
                raise ValidationError(_("A discovery checkpoint must contain between 1 and 10 listing pages."))
            if not 1 <= run.max_items_per_page <= MAX_DISCOVERY_PAGE_ITEMS:
                raise ValidationError(_("A discovery listing page must contain between 1 and 100 observations."))
            if not 1 <= run.max_pages_total <= MAX_DISCOVERY_TOTAL_PAGES:
                raise ValidationError(_("A discovery run must contain between 1 and 10,000,000 listing pages."))

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
                "cursor_kind": "frontier",
                "frontier_migrated": True,
                "parser_version": (values.get("parser_version") or "sparex-listing-frontier-v2").strip(),
                "schema_version": (values.get("schema_version") or "1.1").strip(),
                "plan_artifact_uri": plan_uri,
                "plan_sha256": plan_sha,
                "throttle_seconds": max(3.0, float(values.get("throttle_seconds") or 3.0)),
                "max_pages_per_checkpoint": max(
                    1, min(int(values.get("max_pages_per_checkpoint") or 1), MAX_DISCOVERY_CHECKPOINT_PAGES)
                ),
                "max_items_per_page": max(
                    1, min(int(values.get("max_items_per_page") or MAX_DISCOVERY_PAGE_ITEMS), MAX_DISCOVERY_PAGE_ITEMS)
                ),
                "max_pages_total": max(
                    1,
                    min(
                        int(values.get("max_pages_total") or MAX_DISCOVERY_TOTAL_PAGES),
                        MAX_DISCOVERY_TOTAL_PAGES,
                    ),
                ),
            }
        )
        self.env["southern.sparex.discovery.url"].create(
            {
                "run_id": run.id,
                "company_id": run.company_id.id,
                "url": seed_url,
                "url_sha256": seed_sha,
                "state": "active",
                "priority": 0,
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
            "cursor_kind",
            "parser_version",
            "schema_version",
            "plan_artifact_uri",
            "plan_sha256",
            "throttle_seconds",
            "max_pages_per_checkpoint",
            "max_items_per_page",
            "max_pages_total",
            "queued_url_count",
            "visited_url_count",
            "repair_queued_url_count",
            "repair_visited_url_count",
            "page_count",
            "observed_count",
            "matched_count",
            "missing_count",
            "duplicate_count",
            "review_count",
            "corrected_count",
            "stale_count",
            "product_page_count",
            "empty_page_count",
            "last_page_item_count",
            "reconciliation_state",
            "recovery_state",
            "consecutive_failure_count",
            "recovered_lease_count",
            "cooldown_until",
        ]

    @api.model
    def configure_discovery_checkpoint(self, run_id, max_pages_per_checkpoint=5):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        bounded = max(1, min(int(max_pages_per_checkpoint or 1), MAX_DISCOVERY_CHECKPOINT_PAGES))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_run WHERE id = %s FOR UPDATE NOWAIT", [run.id])
        run.invalidate_recordset()
        if run.lease_owner and run.lease_expires_at and run.lease_expires_at > fields.Datetime.now():
            raise UserError(_("Checkpoint capacity cannot change while a discovery lease is active."))
        if run.state in {"completed", "failed", "cancelled"}:
            return run.read(self._worker_fields())[0]
        updates = {}
        if run.max_pages_per_checkpoint != bounded:
            updates["max_pages_per_checkpoint"] = bounded
        # Existing runs keep the ceiling that was stored when they were
        # created.  Raise legacy active runs to the current safe frontier
        # ceiling so a module upgrade does not strand them at 5,000 pages.
        if run.max_pages_total < MAX_DISCOVERY_TOTAL_PAGES:
            updates["max_pages_total"] = MAX_DISCOVERY_TOTAL_PAGES
        if updates:
            run.write(updates)
        return run.read(self._worker_fields())[0]

    @api.model
    def prepare_reconciliation_run(self, run_id):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_run WHERE id = %s FOR UPDATE NOWAIT", [run.id])
        run.invalidate_recordset()
        if run.reconciliation_state != "pending":
            return run.read(self._worker_fields())[0]
        now = fields.Datetime.now()
        # A new sweep must not demote evidence accepted by the preceding sweep.
        # last_seen_run_id isolates observations for absence reconciliation, and
        # only a completed sweep is allowed to mark unseen records stale.
        run.write(
            {
                "reconciliation_state": "in_progress",
                "reconciliation_started_at": now,
                "reconciliation_completed_at": False,
                "stale_count": 0,
            }
        )
        return run.read(self._worker_fields())[0]

    def _complete_reconciliation(self):
        self.ensure_one()
        if self.reconciliation_state == "completed":
            return self.stale_count
        Item = self.env["southern.sparex.discovery.item"]
        now = fields.Datetime.now()
        Item.flush_model(
            [
                "reconciliation_state",
                "state",
                "review_reason",
                "source_enrichment_candidate",
                "publication_candidate",
            ]
        )
        self.env.cr.execute(
            """
            UPDATE southern_sparex_discovery_item
               SET reconciliation_state = 'stale',
                   state = 'review',
                   review_reason = 'stale_not_seen',
                   source_enrichment_candidate = FALSE,
                   publication_candidate = FALSE,
                   write_uid = %s,
                   write_date = %s
             WHERE company_id = %s
               AND last_seen_run_id IS DISTINCT FROM %s
            """,
            [self.env.uid, now, self.company_id.id, self.id],
        )
        stale_count = self.env.cr.rowcount
        self.env.cr.execute(
            """
            UPDATE southern_sparex_discovery_item
               SET reconciliation_state = 'current',
                   write_uid = %s,
                   write_date = %s
             WHERE company_id = %s
               AND last_seen_run_id = %s
            """,
            [self.env.uid, now, self.company_id.id, self.id],
        )
        Item.invalidate_model(
            [
                "reconciliation_state",
                "state",
                "review_reason",
                "source_enrichment_candidate",
                "publication_candidate",
            ]
        )
        self.write(
            {
                "reconciliation_state": "completed",
                "reconciliation_completed_at": now,
                "stale_count": stale_count,
            }
        )
        return stale_count

    def _ensure_normalized_frontier(self):
        """Lazily move legacy text checkpoints into indexed queue rows."""
        self.ensure_one()
        if self.frontier_migrated:
            return
        URL = self.env["southern.sparex.discovery.url"].sudo()
        values_by_hash = {}
        for url_hash in (self.visited_url_sha256s or "").splitlines():
            url_hash = url_hash.strip().casefold()
            if SHA256_PATTERN.fullmatch(url_hash):
                values_by_hash[url_hash] = {
                    "run_id": self.id,
                    "company_id": self.company_id.id,
                    "url": False,
                    "url_sha256": url_hash,
                    "state": "visited",
                    "priority": 0,
                }
        for url in (self.frontier_urls or "").splitlines():
            url = url.strip()
            if not _sparex_listing_url(url):
                continue
            url_hash = _sha256_text(url)
            if url_hash not in values_by_hash:
                values_by_hash[url_hash] = {
                    "run_id": self.id,
                    "company_id": self.company_id.id,
                    "url": url,
                    "url_sha256": url_hash,
                    "state": "queued",
                    "priority": 0,
                }
        cursor_hash = self.cursor_url_sha256
        if self.state not in {"completed", "failed", "cancelled"} and _sparex_listing_url(self.cursor_url):
            values_by_hash[cursor_hash] = {
                "run_id": self.id,
                "company_id": self.company_id.id,
                "url": self.cursor_url,
                "url_sha256": cursor_hash,
                "state": "active",
                "priority": 0,
            }
        elif cursor_hash in values_by_hash:
            values_by_hash[cursor_hash]["url"] = self.cursor_url
        existing_hashes = set(
            URL.search([("run_id", "=", self.id), ("url_sha256", "in", list(values_by_hash))]).mapped(
                "url_sha256"
            )
        )
        create_values = [
            values for url_hash, values in values_by_hash.items() if url_hash not in existing_hashes
        ]
        if create_values:
            URL.create(create_values)
        self.write(
            {
                "frontier_migrated": True,
                "frontier_urls": False,
                "visited_url_sha256s": False,
                "queued_url_count": URL.search_count(
                    [("run_id", "=", self.id), ("state", "in", ["queued", "active"])]
                ),
                "visited_url_count": URL.search_count(
                    [("run_id", "=", self.id), ("state", "=", "visited")]
                ),
            }
        )

    def _select_next_discovery_url(self, page_limit_reached=False):
        self.ensure_one()
        URL = self.env["southern.sparex.discovery.url"].sudo()
        repair = URL.search(
            [
                ("run_id", "=", self.id),
                ("repair_requested", "=", True),
                ("url", "!=", False),
            ],
            order="repair_priority desc, repair_requested_at, id",
            limit=1,
        )
        selected = repair
        cursor_kind = "repair" if repair else "frontier"
        if not selected and not page_limit_reached:
            selected = URL.search(
                [("run_id", "=", self.id), ("state", "=", "queued"), ("url", "!=", False)],
                order="priority, id",
                limit=1,
            )
            if selected:
                selected.state = "active"
        return selected, cursor_kind

    @api.model
    def queue_discovery_page_repairs(self, run_id, page_urls, reason, priority=100):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        urls = list(dict.fromkeys((value or "").strip() for value in (page_urls or []) if value))
        if not urls or len(urls) > MAX_DISCOVERY_REPAIR_BATCH or not (reason or "").strip():
            raise UserError(_("Discovery repair batches require 1 to 500 explicit listing URLs and a reason."))
        if any(not _sparex_listing_url(url) for url in urls):
            raise UserError(_("Discovery repair requests must contain only HTTPS Sparex listing URLs."))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_run WHERE id = %s FOR UPDATE NOWAIT", [run.id])
        run.invalidate_recordset()
        now = fields.Datetime.now()
        if run.lease_owner and run.lease_expires_at and run.lease_expires_at > now:
            raise UserError(_("Discovery repairs cannot be queued while a worker owns the run lease."))
        run._ensure_normalized_frontier()
        URL = self.env["southern.sparex.discovery.url"].sudo()
        hashes = {_sha256_text(url): url for url in urls}
        existing = URL.search([("run_id", "=", run.id), ("url_sha256", "in", list(hashes))])
        by_hash = {row.url_sha256: row for row in existing}
        page_hashes = set(
            self.env["southern.sparex.discovery.page"]
            .sudo()
            .search([("run_id", "=", run.id), ("page_url_sha256", "in", list(hashes))])
            .mapped("page_url_sha256")
        )
        create_values = []
        repair_count_delta = 0
        queued_count_delta = 0
        visited_count_delta = 0
        for url_hash, url in hashes.items():
            row = by_hash.get(url_hash)
            values = {
                "url": url,
                "repair_requested": True,
                "repair_priority": max(0, min(int(priority or 0), 10_000)),
                "repair_reason": (reason or "").strip()[:255],
                "repair_requested_at": now,
            }
            if row:
                repair_count_delta += 0 if row.repair_requested else 1
                row.write(values)
            else:
                repair_count_delta += 1
                if url_hash in page_hashes:
                    visited_count_delta += 1
                else:
                    queued_count_delta += 1
                create_values.append(
                    {
                        **values,
                        "run_id": run.id,
                        "company_id": run.company_id.id,
                        "url_sha256": url_hash,
                        "state": "visited" if url_hash in page_hashes else "queued",
                        "priority": 0,
                    }
                )
        if create_values:
            URL.create(create_values)
        active_frontier = URL.search(
            [("run_id", "=", run.id), ("state", "=", "active")], limit=1
        )
        if active_frontier:
            active_frontier.state = "queued"
        selected, cursor_kind = run._select_next_discovery_url()
        counts = {
            "queued_url_count": run.queued_url_count + queued_count_delta,
            "visited_url_count": run.visited_url_count + visited_count_delta,
            "repair_queued_url_count": run.repair_queued_url_count + repair_count_delta,
            "repair_visited_url_count": run.repair_visited_url_count,
        }
        if selected:
            run.write(
                {
                    "state": "ready",
                    "cursor_url": selected.url,
                    "cursor_url_sha256": selected.url_sha256,
                    "cursor_kind": cursor_kind,
                    "completed_at": False,
                    "error_code": False,
                    "error_message": False,
                    **counts,
                }
            )
        else:
            run.write(counts)
        return {"queued": len(urls), "cursor_kind": run.cursor_kind, **counts}

    @api.model
    def prepare_legacy_page_url_backfill(self, run_id, limit=50):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        bounded = max(1, min(int(limit or 50), 200))
        pages = self.env["southern.sparex.discovery.page"].sudo().search(
            [
                ("run_id", "=", run.id),
                ("page_url", "=", False),
                ("artifact_uri", "like", "s3://"),
            ],
            order="page_number, id",
            limit=bounded,
        )
        return [
            {
                "page_id": page.id,
                "page_url_sha256": page.page_url_sha256,
                "artifact_uri": page.artifact_uri,
                "artifact_sha256": page.artifact_sha256,
            }
            for page in pages
        ]

    @api.model
    def apply_legacy_page_url_backfill(self, run_id, records):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        records = list(records or [])
        if not records or len(records) > 200:
            raise UserError(_("Legacy page URL backfills must contain between 1 and 200 records."))
        pages = self.env["southern.sparex.discovery.page"].sudo().browse(
            [int(record.get("page_id") or 0) for record in records]
        ).exists()
        page_by_id = {page.id: page for page in pages}
        updated = 0
        for record in records:
            page = page_by_id.get(int(record.get("page_id") or 0))
            page_url = (record.get("page_url") or "").strip()
            if (
                not page
                or page.run_id != run
                or page.artifact_uri != (record.get("artifact_uri") or "").strip()
                or page.artifact_sha256 != (record.get("artifact_sha256") or "").strip().casefold()
                or not _sparex_listing_url(page_url)
                or page.page_url_sha256 != _sha256_text(page_url)
            ):
                raise UserError(_("A legacy page URL backfill does not match its archived evidence."))
            if not page.page_url:
                page.page_url = page_url
                updated += 1
        return {"updated": updated}

    @api.model
    def queue_due_discovery_page_repairs(self, run_id, limit=5, min_age_hours=24):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The discovery run does not exist in the active company."))
        bounded = max(1, min(int(limit or 5), MAX_DISCOVERY_CHECKPOINT_PAGES))
        now = fields.Datetime.now()
        if run.lease_owner and run.lease_expires_at and run.lease_expires_at > now:
            return {"queued": 0, "state": "busy"}
        cutoff = now - timedelta(hours=max(1, min(int(min_age_hours or 24), 24 * 30)))
        items = self.env["southern.sparex.discovery.item"].sudo().search(
            [
                ("company_id", "=", run.company_id.id),
                ("last_seen_run_id", "=", run.id),
                ("state", "=", "review"),
                ("last_seen_at", "<=", cutoff),
                ("last_seen_page_id.page_url", "!=", False),
                "|",
                ("last_seen_page_id.last_repair_at", "=", False),
                ("last_seen_page_id.last_repair_at", "<=", cutoff),
            ],
            order="last_seen_at, id",
            limit=bounded * MAX_DISCOVERY_PAGE_ITEMS,
        )
        page_urls = []
        seen_page_ids = set()
        for item in items:
            page = item.last_seen_page_id
            if not page or page.id in seen_page_ids:
                continue
            seen_page_ids.add(page.id)
            page_urls.append(page.page_url)
            if len(page_urls) >= bounded:
                break
        if not page_urls:
            return {"queued": 0, "state": run.state}
        return self.queue_discovery_page_repairs(
            run.id,
            page_urls,
            _("Automatically revisit stale or ambiguous listing evidence."),
            priority=50,
        )

    @api.model
    def claim_discovery_checkpoint(self, run_id, worker_id, lease_seconds=180):
        run = self.browse(int(run_id)).exists()
        if not run or run.company_id != self.env.company:
            raise UserError(_("The requested discovery run does not exist in the active company."))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_run WHERE id = %s FOR UPDATE NOWAIT", [run.id])
        run.invalidate_recordset()
        run._ensure_normalized_frontier()
        now = fields.Datetime.now()
        if run.state in {"completed", "failed", "cancelled"}:
            return {"claimed": False, "state": run.state}
        if run.state == "cooldown" and run.cooldown_until and run.cooldown_until > now:
            return {"claimed": False, "state": run.state, "cooldown_until": run.cooldown_until}
        if run.lease_owner and run.lease_expires_at and run.lease_expires_at > now and run.lease_owner != worker_id:
            return {"claimed": False, "state": "busy"}
        recovered_lease = bool(run.lease_owner and run.lease_expires_at and run.lease_expires_at <= now)
        run.write(
            {
                "state": "running",
                "lease_owner": (worker_id or "external-worker")[:255],
                "lease_expires_at": now + timedelta(seconds=max(60, min(int(lease_seconds or 180), 900))),
                "cooldown_until": False,
                "error_code": False,
                "error_message": False,
                "recovered_lease_count": run.recovered_lease_count + (1 if recovered_lease else 0),
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
        listing_urls = list(page.get("listing_urls") or [])
        if page_url != run.cursor_url or not _https_sparex_url(page_url):
            raise UserError(_("The listing page does not match the explicit discovery cursor."))
        if not SHA256_PATTERN.fullmatch(page_sha) or not SHA256_PATTERN.fullmatch(artifact_sha) or not artifact_uri:
            raise UserError(_("The listing checkpoint requires checksum-verified evidence."))
        if len(items) > run.max_items_per_page:
            raise UserError(_("The listing page exceeds the bounded discovery item limit."))
        if len(listing_urls) > MAX_DISCOVERY_LINKS_PER_PAGE:
            raise UserError(_("The listing page exceeds the bounded discovery frontier limit."))
        page_url_sha = _sha256_text(page_url)
        existing_page = self.env["southern.sparex.discovery.page"].search(
            [("run_id", "=", run.id), ("page_url_sha256", "=", page_url_sha)], limit=1
        )
        repairing = run.cursor_kind == "repair"
        if existing_page and not repairing:
            run.write({"lease_owner": False, "lease_expires_at": False})
            return {"idempotent": True, "page_id": existing_page.id, "state": run.state}

        now = fields.Datetime.now()
        page_record = existing_page
        if not page_record:
            page_record = self.env["southern.sparex.discovery.page"].create(
                {
                    "run_id": run.id,
                    "company_id": run.company_id.id,
                    "page_number": run.page_count + 1,
                    "page_url": page_url,
                    "page_url_sha256": page_url_sha,
                    "page_sha256": page_sha,
                    "artifact_uri": artifact_uri,
                    "artifact_sha256": artifact_sha,
                    "retrieved_at": now,
                }
            )
        seen_skus = set()
        normalized_observations = []
        for observation in items:
            observation = dict(observation or {})
            raw_sku = (observation.get("sku") or "").strip()
            normalized = normalized_sparex_sku(raw_sku)
            listing_title = " ".join((observation.get("listing_title") or "").split()).strip()
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
            if len(listing_title) > 255:
                raise UserError(_("A listing observation title exceeds the bounded product-name length."))
            normalized_observations.append(
                {
                    "raw_sku": raw_sku,
                    "normalized": normalized,
                    "listing_title": listing_title,
                    "source_url": source_url,
                    "image_url": image_url,
                    "source_state": source_state,
                }
            )

        normalized_skus = [row["normalized"] for row in normalized_observations]
        candidate_codes = sorted(
            {
                candidate
                for normalized in normalized_skus
                for candidate in (normalized, normalized.replace(".", "", 1))
            }
        )
        product_ids_by_sku = {normalized: [] for normalized in normalized_skus}
        if candidate_codes:
            self.env.cr.execute(
                """
                SELECT id
                  FROM product_template
                 WHERE UPPER(REPLACE(COALESCE(default_code, ''), ' ', '')) = ANY(%s)
                """,
                [candidate_codes],
            )
            candidate_products = self.env["product.template"].with_context(active_test=False).browse(
                [row[0] for row in self.env.cr.fetchall()]
            )
            for product in candidate_products:
                normalized = normalized_sparex_sku(product.default_code)
                if normalized in product_ids_by_sku:
                    product_ids_by_sku[normalized].append(product.id)

        matched_product_ids = [
            product_ids[0]
            for product_ids in product_ids_by_sku.values()
            if len(product_ids) == 1
        ]
        supplier_by_product_id = {}
        if matched_product_ids:
            suppliers = self.env["product.supplierinfo"].sudo().search(
                [
                    ("product_tmpl_id", "in", matched_product_ids),
                    ("partner_id.name", "=ilike", "Sparex"),
                    ("price", ">", 0),
                ],
                order="id",
            )
            for supplier in suppliers:
                supplier_by_product_id.setdefault(supplier.product_tmpl_id.id, supplier)

        Item = self.env["southern.sparex.discovery.item"].with_context(
            tracking_disable=True,
            mail_create_nolog=True,
        )
        existing_items = Item.search(
            [("company_id", "=", run.company_id.id), ("normalized_sku", "in", normalized_skus)]
        )
        existing_item_by_sku = {item.normalized_sku: item for item in existing_items}
        page_counts = {"matched": 0, "missing": 0, "duplicate": 0, "review": 0}
        page_corrected = 0
        page_item_ids = []
        vendor_catalog_records = []
        for observation in normalized_observations:
            raw_sku = observation["raw_sku"]
            normalized = observation["normalized"]
            listing_title = observation["listing_title"]
            source_url = observation["source_url"]
            image_url = observation["image_url"]
            source_state = observation["source_state"]
            exact_ids = product_ids_by_sku.get(normalized, [])
            exact = self.env["product.template"].with_context(active_test=False).browse(exact_ids)
            if len(exact_ids) > 1:
                match_state = "duplicate"
                matched_product = self.env["product.template"]
            elif exact_ids:
                matched_product = exact[:1]
                match_state = "matched_active" if matched_product.active else "matched_archived"
            else:
                matched_product = self.env["product.template"]
                match_state = "missing"
            positive_supplier = supplier_by_product_id.get(
                matched_product.id if matched_product else 0,
                self.env["product.supplierinfo"],
            )
            queue_state = "verified" if source_state == "verified" and image_url else "review"
            has_cost = bool(positive_supplier)
            has_sales_price = bool(matched_product and not sales_price_blocker(matched_product, positive_supplier))
            has_exact_url = exact_sparex_url(source_url, normalized)
            has_image = bool(image_url)
            currently_published = bool(matched_product and matched_product.website_published)
            product_has_exact_url = bool(
                matched_product and exact_sparex_url(matched_product.southern_source_url, normalized)
            )
            product_has_image = bool(matched_product and matched_product.image_1920)
            product_has_category = bool(matched_product and matched_product.public_categ_ids)
            product_has_description = bool(matched_product and customer_description_ready(matched_product))
            source_enrichment_candidate = bool(
                matched_product
                and matched_product.active
                and not currently_published
                and has_cost
                and has_sales_price
                and has_exact_url
                and has_image
                and queue_state == "verified"
                and (not product_has_exact_url or not product_has_image)
            )
            publication_candidate = bool(
                matched_product
                and matched_product.active
                and not currently_published
                and has_cost
                and has_sales_price
                and has_exact_url
                and has_image
                and queue_state == "verified"
                and product_has_exact_url
                and product_has_image
                and product_has_category
                and product_has_description
            )
            item = existing_item_by_sku.get(normalized, Item)
            review_reason = False
            if source_state == "ambiguous":
                review_reason = "source_ambiguous"
            elif source_state == "missing_image" or not image_url:
                review_reason = "missing_image"
            elif match_state == "missing":
                review_reason = "missing_odoo"
            elif match_state == "duplicate":
                review_reason = "duplicate_odoo"
            primary_blocker = _primary_publication_blocker(
                reconciliation_state="current",
                item_state=queue_state,
                source_state=source_state,
                match_state=match_state,
                product_active=bool(matched_product and matched_product.active),
                currently_published=currently_published,
                has_cost=has_cost,
                has_sales_price=has_sales_price,
                product_has_exact_url=product_has_exact_url,
                product_has_image=product_has_image,
                product_has_category=product_has_category,
                product_has_description=product_has_description,
            )
            corrected = bool(
                item
                and queue_state == "verified"
                and (
                    item.state != "verified"
                    or item.source_state != "verified"
                    or item.source_url_sha256 != _sha256_text(source_url)
                    or item.image_url_sha256 != (_sha256_text(image_url) if image_url else False)
                )
            )
            values = {
                "raw_sku": raw_sku,
                "listing_title": (
                    item.listing_title
                    if item and item.detail_title_sha256
                    else (listing_title or False)
                ),
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
                "primary_blocker": primary_blocker,
                "readiness_refreshed_at": now,
                "reconciliation_state": "current",
                "review_reason": review_reason,
                "creation_state": (
                    "created"
                    if item and item.creation_state == "created" and match_state.startswith("matched_")
                    else ("review_required" if match_state == "missing" else "not_authorized")
                ),
                "last_seen_run_id": run.id,
                "last_seen_page_id": page_record.id,
                "last_seen_at": now,
                "source_artifact_uri": artifact_uri,
                "source_artifact_sha256": artifact_sha,
            }
            if item:
                values["observation_count"] = item.observation_count + 1
                if corrected:
                    values["correction_count"] = item.correction_count + 1
                    values["last_corrected_at"] = now
                    page_corrected += 1
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
            page_item_ids.append(item.id)
            vendor_catalog_records.append(
                {
                    "vendor_sku": raw_sku,
                    "normalized_sku": normalized,
                    "title": listing_title,
                    "customer_description": listing_title,
                    "source_url": source_url,
                    "image_url": image_url,
                    "vendor_cost": positive_supplier.price if positive_supplier else 0.0,
                    "sales_price": matched_product.list_price if matched_product else 0.0,
                    "availability": "available",
                }
            )
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
                "page_url": page_url,
                "page_sha256": page_sha,
                "artifact_uri": artifact_uri,
                "artifact_sha256": artifact_sha,
                "retrieved_at": now,
                "item_count": len(items),
                "matched_count": page_counts["matched"],
                "missing_count": page_counts["missing"],
                "duplicate_count": page_counts["duplicate"],
                "review_count": page_counts["review"],
                "repair_visit_count": page_record.repair_visit_count + (1 if repairing else 0),
                "last_repair_at": now if repairing else page_record.last_repair_at,
            }
        )
        staged = {"created": 0, "updated": 0, "unchanged": 0, "ready": 0, "observed": 0}
        if vendor_catalog_records:
            staged = self.env["southern.vendor.catalog.item"].sudo().upsert_catalog_items(
                source_code="sparex",
                records=vendor_catalog_records,
                artifact_uri=artifact_uri,
                artifact_sha256=artifact_sha,
                schema_version="1.0",
            )
        next_url = (page.get("next_url") or "").strip()
        if next_url and not _sparex_listing_url(next_url):
            raise UserError(_("The next listing cursor is not an HTTPS Sparex URL."))
        run._ensure_normalized_frontier()
        URL = self.env["southern.sparex.discovery.url"].sudo()
        current_url = URL.search(
            [("run_id", "=", run.id), ("url_sha256", "=", page_url_sha)], limit=1
        )
        current_created = not current_url
        if not current_url:
            current_url = URL.create(
                {
                    "run_id": run.id,
                    "company_id": run.company_id.id,
                    "url": page_url,
                    "url_sha256": page_url_sha,
                    "state": "visited" if repairing else "active",
                }
            )
        current_was_queued = current_url.state in {"queued", "active"}
        repair_was_requested = bool(current_url.repair_requested)
        first_repair_visit = bool(repairing and not current_url.repair_visit_count)
        if repairing:
            current_url.write(
                {
                    "url": page_url,
                    "state": "visited",
                    "repair_requested": False,
                    "repair_reason": False,
                    "repair_requested_at": False,
                    "repair_visit_count": current_url.repair_visit_count + 1,
                    "last_repair_at": now,
                }
            )
        else:
            current_url.write({"url": page_url, "state": "visited", "last_visited_at": now})
        candidate_urls = []
        for candidate in listing_urls:
            candidate = (candidate or "").strip()
            if not candidate:
                continue
            if not _sparex_listing_url(candidate):
                raise UserError(_("A discovered frontier URL is not an HTTPS Sparex listing URL."))
            candidate_urls.append(candidate)
        if next_url:
            candidate_urls.append(next_url)
        candidate_by_hash = {_sha256_text(url): url for url in candidate_urls}
        existing_candidates = URL.search(
            [("run_id", "=", run.id), ("url_sha256", "in", list(candidate_by_hash))]
        )
        existing_hashes = set(existing_candidates.mapped("url_sha256"))
        if next_url:
            next_hash = _sha256_text(next_url)
            existing_next = existing_candidates.filtered(lambda row: row.url_sha256 == next_hash)
            if existing_next and existing_next.state == "queued":
                existing_next.priority = -100
        create_values = []
        for candidate_sha, candidate in candidate_by_hash.items():
            if candidate_sha in existing_hashes:
                continue
            create_values.append(
                {
                    "run_id": run.id,
                    "company_id": run.company_id.id,
                    "url": candidate,
                    "url_sha256": candidate_sha,
                    "state": "queued",
                    "priority": (
                        -100
                        if candidate == next_url
                        else (_frontier_priority(candidate)[0] * 1_000 + _frontier_priority(candidate)[1])
                    ),
                }
            )
        if create_values:
            URL.create(create_values)
        next_page_count = run.page_count + (0 if repairing else 1)
        page_limit_reached = next_page_count >= run.max_pages_total
        selected, cursor_kind = run._select_next_discovery_url(page_limit_reached=page_limit_reached)
        completed = not selected
        queue_counts = {
            "queued_url_count": max(
                0,
                run.queued_url_count - (1 if current_was_queued else 0) + len(create_values),
            ),
            "visited_url_count": run.visited_url_count
            + (1 if current_was_queued or current_created else 0),
            "repair_queued_url_count": max(
                0,
                run.repair_queued_url_count - (1 if repair_was_requested else 0),
            ),
            "repair_visited_url_count": run.repair_visited_url_count
            + (1 if first_repair_visit else 0),
        }
        run.write(
            {
                "state": "completed" if completed else "ready",
                "cursor_url": selected.url if selected else page_url,
                "cursor_url_sha256": selected.url_sha256 if selected else page_url_sha,
                "cursor_kind": cursor_kind,
                **queue_counts,
                "page_count": next_page_count,
                "observed_count": run.observed_count + (0 if repairing else len(items)),
                "matched_count": run.matched_count + (0 if repairing else page_counts["matched"]),
                "missing_count": run.missing_count + (0 if repairing else page_counts["missing"]),
                "duplicate_count": run.duplicate_count + (0 if repairing else page_counts["duplicate"]),
                "review_count": run.review_count + (0 if repairing else page_counts["review"]),
                "corrected_count": run.corrected_count + page_corrected,
                "product_page_count": run.product_page_count + (0 if repairing or not items else 1),
                "empty_page_count": run.empty_page_count + (0 if repairing or items else 1),
                "last_page_item_count": len(items),
                "last_request_at": now,
                "last_success_at": now,
                "consecutive_failure_count": 0,
                "recovery_state": "healthy",
                "next_request_at": False if completed else now + timedelta(seconds=run.throttle_seconds),
                "completed_at": now if completed else False,
                "error_code": (
                    "max_pages_total_reached"
                    if page_limit_reached and queue_counts["queued_url_count"]
                    else False
                ),
                "lease_owner": False,
                "lease_expires_at": False,
            }
        )
        if completed:
            run._complete_reconciliation()
        return {
            "idempotent": False,
            "repair": repairing,
            "page_id": page_record.id,
            "state": run.state,
            "next_cursor_sha256": run.cursor_url_sha256,
            "counts": page_counts,
            "observed": len(items),
            "corrected": page_corrected,
            "item_ids": page_item_ids,
            "vendor_catalog": staged,
            "stale": run.stale_count if completed else 0,
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
                "last_failure_at": now,
                "consecutive_failure_count": run.consecutive_failure_count + 1,
                "recovery_state": "cooldown" if cooldown else "retrying",
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
    page_url = fields.Char(readonly=True, copy=False)
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
    repair_visit_count = fields.Integer(default=0, readonly=True)
    last_repair_at = fields.Datetime(readonly=True)

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


class SouthernSparexDiscoveryUrl(models.Model):
    _name = "southern.sparex.discovery.url"
    _description = "Sparex Discovery URL Queue"
    _order = "repair_requested desc, repair_priority desc, priority, id"

    run_id = fields.Many2one("southern.sparex.discovery.run", required=True, ondelete="cascade", index=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    url = fields.Char(readonly=True, copy=False)
    url_sha256 = fields.Char(required=True, readonly=True, copy=False, index=True)
    state = fields.Selection(
        [("queued", "Queued"), ("active", "Active"), ("visited", "Visited")],
        default="queued",
        required=True,
        readonly=True,
        index=True,
    )
    priority = fields.Integer(default=0, readonly=True, index=True)
    repair_requested = fields.Boolean(default=False, readonly=True, index=True)
    repair_priority = fields.Integer(default=0, readonly=True, index=True)
    repair_reason = fields.Char(readonly=True)
    repair_requested_at = fields.Datetime(readonly=True, index=True)
    repair_visit_count = fields.Integer(default=0, readonly=True)
    last_visited_at = fields.Datetime(readonly=True, index=True)
    last_repair_at = fields.Datetime(readonly=True, index=True)

    _run_url_unique = models.Constraint(
        "unique(run_id, url_sha256)", "Each listing URL can appear only once in a discovery run queue."
    )

    def init(self):
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS southern_sparex_discovery_url_frontier_idx
                ON southern_sparex_discovery_url (run_id, priority, id)
             WHERE state = 'queued' AND url IS NOT NULL
            """
        )
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS southern_sparex_discovery_url_repair_idx
                ON southern_sparex_discovery_url
                    (run_id, repair_priority DESC, repair_requested_at, id)
             WHERE repair_requested IS TRUE AND url IS NOT NULL
            """
        )

    @api.constrains("url", "url_sha256")
    def _check_url_contract(self):
        for row in self:
            if not SHA256_PATTERN.fullmatch((row.url_sha256 or "").casefold()):
                raise ValidationError(_("Discovery queue URL hashes must be SHA-256 hexadecimal values."))
            if row.url and (not _sparex_listing_url(row.url) or _sha256_text(row.url) != row.url_sha256):
                raise ValidationError(_("Discovery queue URLs must be checksum-matched HTTPS Sparex listing URLs."))


class SouthernSparexDiscoveryItem(models.Model):
    _name = "southern.sparex.discovery.item"
    _description = "Sparex Catalog Discovery Queue"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model metadata
    _order = "odoo_match_state, normalized_sku, id"

    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    normalized_sku = fields.Char(required=True, readonly=True, index=True, tracking=True)
    raw_sku = fields.Char(required=True, readonly=True)
    listing_title = fields.Char(readonly=True)
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
    primary_blocker = fields.Selection(
        [
            ("ready", "Ready to Publish"),
            ("stale", "Awaiting Current Rescan"),
            ("source_review", "Source Evidence Review"),
            ("missing_odoo", "Missing in Odoo"),
            ("duplicate_odoo", "Duplicate Odoo SKU"),
            ("archived", "Product Archived"),
            ("already_published", "Already Published"),
            ("missing_cost", "Missing Positive Dealer Cost"),
            ("missing_sales_price", "Missing Positive Sales Price"),
            ("missing_product_url", "Missing Product Sparex URL"),
            ("missing_product_image", "Missing Product Image"),
            ("missing_customer_description", "Missing Customer Description"),
        ],
        readonly=True,
        index=True,
    )
    readiness_refreshed_at = fields.Datetime(readonly=True, index=True)
    cost_recovery_state = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("queued", "Queued"),
            ("claimed", "Claimed"),
            ("retry_wait", "Retry Scheduled"),
            ("resolved", "Resolved"),
            ("manual_review", "Manual Review"),
        ],
        default="not_required",
        required=True,
        readonly=True,
        index=True,
        tracking=True,
    )
    cost_recovery_priority = fields.Integer(default=0, readonly=True, index=True)
    cost_recovery_attempt_count = fields.Integer(default=0, readonly=True)
    cost_recovery_next_at = fields.Datetime(readonly=True, index=True)
    cost_recovery_claimed_at = fields.Datetime(readonly=True)
    cost_recovery_worker_id = fields.Char(readonly=True, copy=False, index=True)
    cost_recovery_last_error = fields.Char(readonly=True, copy=False)
    cost_evidence_sha256 = fields.Char(readonly=True, copy=False)
    cost_evidence_url_sha256 = fields.Char(readonly=True, copy=False)
    cost_recovery_parser_version = fields.Char(readonly=True, copy=False)
    cost_recovered_at = fields.Datetime(readonly=True)
    detail_title_sha256 = fields.Char(readonly=True, copy=False)
    detail_title_page_sha256 = fields.Char(readonly=True, copy=False)
    detail_title_parser_version = fields.Char(readonly=True, copy=False)
    detail_title_recovered_at = fields.Datetime(readonly=True)
    detail_page_artifact_uri = fields.Char(readonly=True, copy=False)
    detail_page_artifact_sha256 = fields.Char(readonly=True, copy=False)
    reconciliation_state = fields.Selection(
        [("pending", "Awaiting Current Run"), ("current", "Current Run Verified"), ("stale", "Not Seen")],
        default="pending",
        required=True,
        readonly=True,
        index=True,
    )
    review_reason = fields.Selection(
        [
            ("source_ambiguous", "Ambiguous Listing"),
            ("missing_image", "Missing Listing Image"),
            ("missing_odoo", "Missing in Odoo"),
            ("duplicate_odoo", "Duplicate Odoo SKU"),
            ("stale_not_seen", "Not Seen in Current Catalog Run"),
        ],
        readonly=True,
        index=True,
    )
    correction_count = fields.Integer(default=0, readonly=True)
    last_corrected_at = fields.Datetime(readonly=True)
    blocker_summary = fields.Char(compute="_compute_blocker_summary")
    creation_state = fields.Selection(
        [
            ("not_authorized", "Product Creation Not Authorized"),
            ("review_required", "Creation Review Required"),
            ("approved", "Approved for Separate Creation Workflow"),
            ("created", "Draft Product Created"),
            ("rejected", "Creation Rejected"),
        ],
        default="not_authorized",
        required=True,
        readonly=True,
        tracking=True,
        index=True,
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

    def init(self):
        self.env.cr.execute("DROP INDEX IF EXISTS southern_sparex_discovery_item_release_idx")
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS southern_sparex_discovery_item_release_idx
                ON southern_sparex_discovery_item
                    (company_id, cost_recovery_priority DESC, readiness_refreshed_at, id)
             WHERE reconciliation_state = 'current'
               AND odoo_match_state = 'matched_active'
            """
        )
        self.env.cr.execute(
            """
            CREATE INDEX IF NOT EXISTS southern_sparex_discovery_item_refresh_idx
                ON southern_sparex_discovery_item
                    (company_id, readiness_refreshed_at, id)
             WHERE reconciliation_state = 'current'
               AND odoo_match_state = 'matched_active'
            """
        )

    @api.depends(
        "reconciliation_state",
        "review_reason",
        "has_positive_supplier_cost",
        "has_positive_sales_price",
        "has_exact_sparex_url",
        "has_image",
        "odoo_match_state",
    )
    def _compute_blocker_summary(self):
        labels = {
            "source_ambiguous": _("ambiguous source"),
            "missing_image": _("missing listing image"),
            "missing_odoo": _("missing in Odoo"),
            "duplicate_odoo": _("duplicate Odoo SKU"),
            "stale_not_seen": _("not seen in current run"),
        }
        for item in self:
            blockers = []
            if item.reconciliation_state != "current":
                blockers.append(_("stale evidence"))
            if item.review_reason:
                blockers.append(labels.get(item.review_reason, item.review_reason))
            if not item.has_positive_supplier_cost:
                blockers.append(_("supplier cost"))
            if not item.has_positive_sales_price:
                blockers.append(_("sales price"))
            if not item.has_exact_sparex_url:
                blockers.append(_("exact URL"))
            if not item.has_image:
                blockers.append(_("image"))
            item.blocker_summary = ", ".join(dict.fromkeys(blockers)) or _("Ready")

    def action_approve_creation_review(self):
        missing = self.filtered(lambda item: item.odoo_match_state == "missing")
        if len(missing) != len(self):
            raise UserError(_("Only Sparex SKUs confirmed missing in Odoo can be approved for creation review."))
        missing.write({"creation_state": "approved"})
        return True

    def action_reject_creation_review(self):
        missing = self.filtered(lambda item: item.odoo_match_state == "missing")
        if len(missing) != len(self):
            raise UserError(_("Only Sparex SKUs confirmed missing in Odoo can be rejected."))
        missing.write({"creation_state": "rejected"})
        return True

    def action_reset_creation_review(self):
        self.filtered(lambda item: item.odoo_match_state == "missing").write({"creation_state": "review_required"})
        self.filtered(lambda item: item.odoo_match_state != "missing").write({"creation_state": "not_authorized"})
        return True

    def _product_creation_snapshot(self):
        self.ensure_one()
        category = self.env.ref(
            "southern_parts_intelligence.product_category_sparex_pending_enrichment",
            raise_if_not_found=False,
        )
        suppliers = (
            self.env["res.partner"]
            .sudo()
            .search([("name", "=ilike", "Sparex"), ("supplier_rank", ">", 0)], limit=2)
        )
        supplier = suppliers if len(suppliers) == 1 else self.env["res.partner"]
        snapshot = {
            "item_id": self.id,
            "company_id": self.company_id.id,
            "sku": self.normalized_sku,
            "listing_title": self.listing_title or "",
            "source_url": self.source_url,
            "source_url_sha256": self.source_url_sha256,
            "image_url": self.image_url or "",
            "image_url_sha256": self.image_url_sha256 or "",
            "source_artifact_uri": self.source_artifact_uri,
            "source_artifact_sha256": self.source_artifact_sha256,
            "item_write_date": str(self.write_date or ""),
            "category_id": category.id if category else None,
            "category_write_date": str(category.write_date or "") if category else "",
            "supplier_partner_count": len(suppliers),
            "supplier_partner_id": supplier.id if supplier else None,
            "supplier_write_date": str(supplier.write_date or "") if supplier else "",
        }
        snapshot["snapshot_sha256"] = _canonical_sha256(snapshot)
        return snapshot

    @api.model
    def prepare_product_creation_plan(self, item_ids=None, limit=MAX_PRODUCT_CREATION_BATCH):
        bounded = max(1, min(int(limit or MAX_PRODUCT_CREATION_BATCH), MAX_PRODUCT_CREATION_BATCH))
        domain = [
            ("company_id", "=", self.env.company.id),
            ("reconciliation_state", "=", "current"),
            ("state", "=", "verified"),
            ("source_state", "=", "verified"),
            ("odoo_match_state", "=", "missing"),
            ("creation_state", "in", ["review_required", "approved"]),
            ("listing_title", "!=", False),
            ("image_url", "!=", False),
        ]
        if item_ids:
            domain.append(("id", "in", [int(item_id) for item_id in item_ids]))
        prepared = []
        for item in self.search(domain, order="last_seen_at, id", limit=bounded * 3):
            snapshot = item._product_creation_snapshot()
            if (
                not snapshot["category_id"]
                or snapshot["supplier_partner_count"] != 1
                or not exact_sparex_url(item.source_url, item.normalized_sku)
                or not item.image_url
                or not item.listing_title
            ):
                continue
            prepared.append(snapshot)
            if len(prepared) >= bounded:
                break
        return prepared

    @api.model
    def apply_product_creation_plan(
        self,
        records,
        plan_artifact_uri,
        plan_sha256,
        confirmation,
        reason,
    ):
        records = list(records or [])
        if confirmation != PRODUCT_CREATION_CONFIRMATION or not (reason or "").strip():
            raise UserError(_("Page-driven product creation requires explicit confirmation and a business reason."))
        if not records or len(records) > MAX_PRODUCT_CREATION_BATCH:
            raise UserError(_("Product creation plans must contain between 1 and 5 exact products."))
        if not (plan_artifact_uri or "").startswith("s3://") or not SHA256_PATTERN.fullmatch(
            (plan_sha256 or "").casefold()
        ):
            raise UserError(_("Product creation requires an archived SHA-256 plan artifact."))
        authorized_syncs = (
            self.env["southern.parts.catalog.sync"]
            .sudo()
            .search(
                [
                    ("active", "=", True),
                    ("mode", "=", "sparex_discovery"),
                    ("approval_state", "=", "approved"),
                    ("continuous_release_enabled", "=", True),
                    ("page_driven_creation_enabled", "=", True),
                ],
                limit=2,
            )
        )
        if len(authorized_syncs) != 1:
            raise UserError(_("Page-driven product creation is not enabled on one approved Sparex workflow."))
        category = self.env.ref(
            "southern_parts_intelligence.product_category_sparex_pending_enrichment",
            raise_if_not_found=False,
        )
        if not category:
            raise UserError(_("The Sparex pending-enrichment product category is not configured."))
        applied = []
        for prepared in records:
            item = self.browse(int(prepared.get("item_id") or 0)).exists()
            if not item or item.company_id != self.env.company:
                raise UserError(_("The prepared Sparex discovery item is unavailable."))
            self.env.cr.execute(
                "SELECT id FROM southern_sparex_discovery_item WHERE id = %s FOR UPDATE NOWAIT",
                [item.id],
            )
            item.invalidate_recordset()
            current_snapshot = item._product_creation_snapshot()
            if (
                item.reconciliation_state != "current"
                or item.state != "verified"
                or item.source_state != "verified"
                or item.odoo_match_state != "missing"
                or item.creation_state not in {"review_required", "approved"}
                or not item.listing_title
                or not item.image_url
                or not exact_sparex_url(item.source_url, item.normalized_sku)
                or current_snapshot != prepared
                or _canonical_sha256({key: value for key, value in prepared.items() if key != "snapshot_sha256"})
                != (prepared.get("snapshot_sha256") or "").casefold()
            ):
                raise UserError(_("Sparex product creation evidence changed; prepare a fresh plan."))
            digits = item.normalized_sku.split(".", 1)[1]
            candidates = (
                self.env["product.template"]
                .with_context(active_test=False)
                .search(
                    [
                        "|",
                        ("default_code", "ilike", f"S.{digits}"),
                        ("default_code", "ilike", f"S{digits}"),
                    ]
                )
            )
            exact = candidates.filtered(
                lambda product, target=item.normalized_sku: normalized_sparex_sku(product.default_code) == target
            )
            if len(exact) > 1:
                raise UserError(_("The Sparex SKU became duplicated after the creation plan was prepared."))
            created = not bool(exact)
            product = exact[:1]
            supplier_line = self.env["product.supplierinfo"]
            if created:
                product = (
                    self.env["product.template"]
                    .sudo()
                    .create(
                        {
                            "name": item.listing_title,
                            "default_code": item.normalized_sku,
                            "active": True,
                            "sale_ok": True,
                            "purchase_ok": True,
                            "categ_id": category.id,
                            "list_price": 0.0,
                            "standard_price": 0.0,
                            "southern_source_name": "Sparex",
                            "southern_source_url": item.source_url,
                            "southern_enrichment_status": "partial",
                            "website_published": False,
                        }
                    )
                )
                supplier = self.env["res.partner"].sudo().browse(int(prepared["supplier_partner_id"])).exists()
                if (
                    not supplier
                    or supplier.name.casefold() != "sparex"
                    or supplier.supplier_rank <= 0
                    or str(supplier.write_date or "") != prepared["supplier_write_date"]
                ):
                    raise UserError(_("The exact Sparex supplier changed after the creation plan was prepared."))
                supplier_line = self.env["product.supplierinfo"].sudo().create(
                    {
                        "partner_id": supplier.id,
                        "product_tmpl_id": product.id,
                        "product_code": item.normalized_sku,
                        "price": 0.0,
                        "min_qty": 1.0,
                    }
                )
            item.write(
                {
                    "odoo_match_state": "matched_active" if product.active else "matched_archived",
                    "matched_product_id": product.id,
                    "duplicate_product_ids": [(5, 0, 0)],
                    "creation_state": "created" if created else "not_authorized",
                    "review_reason": False,
                }
            )
            item._refresh_readiness()
            applied.append(
                {
                    "item_id": item.id,
                    "product_id": product.id,
                    "sku": item.normalized_sku,
                    "created": created,
                    "website_published": bool(product.website_published),
                    "category_id": product.categ_id.id,
                    "product_write_date": str(product.write_date or ""),
                    "supplierinfo_id": supplier_line.id if supplier_line else None,
                    "supplierinfo_write_date": str(supplier_line.write_date or "") if supplier_line else "",
                    "plan_artifact_uri": plan_artifact_uri,
                    "plan_sha256": plan_sha256.casefold(),
                }
            )
        return applied

    @api.model
    def rollback_created_products(self, records, reason):
        records = list(records or [])
        if not (reason or "").strip() or not records or len(records) > MAX_PRODUCT_CREATION_BATCH:
            raise UserError(_("A bounded product-creation rollback reason is required."))
        category = self.env.ref(
            "southern_parts_intelligence.product_category_sparex_pending_enrichment",
            raise_if_not_found=False,
        )
        rolled_back = []
        for record in records:
            if not record.get("created"):
                raise UserError(_("Only products created by the page-driven plan can be rolled back."))
            item = self.browse(int(record.get("item_id") or 0)).exists()
            product = (
                self.env["product.template"]
                .sudo()
                .with_context(active_test=False)
                .browse(int(record.get("product_id") or 0))
                .exists()
            )
            supplier = (
                self.env["product.supplierinfo"]
                .sudo()
                .browse(int(record.get("supplierinfo_id") or 0))
                .exists()
            )
            if not item or not product or not supplier:
                raise UserError(_("The created product rollback scope is unavailable."))
            self.env.cr.execute("SELECT id FROM product_template WHERE id = %s FOR UPDATE NOWAIT", [product.id])
            product.invalidate_recordset()
            supplier.invalidate_recordset()
            if (
                item.company_id != self.env.company
                or item.matched_product_id != product
                or item.creation_state != "created"
                or product.default_code != item.normalized_sku
                or product.website_published
                or product.categ_id != category
                or product.list_price != 0
                or product.standard_price != 0
                or not exact_sparex_url(product.southern_source_url, item.normalized_sku)
                or supplier.product_tmpl_id != product
                or supplier.partner_id.name.casefold() != "sparex"
                or supplier.price != 0
                or str(product.write_date or "") != record.get("product_write_date")
                or str(supplier.write_date or "") != record.get("supplierinfo_write_date")
            ):
                raise UserError(_("The created product changed after its rollback snapshot; manual review is required."))
            product.write({"active": False})
            item.write(
                {
                    "odoo_match_state": "matched_archived",
                    "creation_state": "rejected",
                    "review_reason": False,
                }
            )
            item._refresh_readiness()
            rolled_back.append({"item_id": item.id, "product_id": product.id, "active": False})
        return rolled_back

    @api.constrains(
        "normalized_sku",
        "source_url",
        "source_url_sha256",
        "image_url",
        "image_url_sha256",
        "source_artifact_sha256",
        "listing_title",
        "detail_title_sha256",
        "detail_title_page_sha256",
        "detail_page_artifact_uri",
        "detail_page_artifact_sha256",
        "detail_title_parser_version",
        "detail_title_recovered_at",
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
                for value in (
                    item.source_url_sha256,
                    item.image_url_sha256,
                    item.source_artifact_sha256,
                    item.detail_title_sha256,
                    item.detail_title_page_sha256,
                    item.detail_page_artifact_sha256,
                )
            ):
                raise ValidationError(_("Discovery evidence hashes must be SHA-256 hexadecimal values."))
            if item.detail_title_sha256 and (
                not _verified_detail_title(item.listing_title)
                or item.detail_title_sha256 != _sha256_text(item.listing_title)
                or not item.detail_title_page_sha256
                or not item.detail_title_parser_version
                or not item.detail_title_recovered_at
            ):
                raise ValidationError(_("Verified detail-title evidence does not match the retained title."))
            if bool(item.detail_title_sha256) != bool(item.detail_title_page_sha256):
                raise ValidationError(_("Detail-title and detail-page evidence hashes must be retained together."))
            if bool(item.detail_page_artifact_uri) != bool(item.detail_page_artifact_sha256) or (
                item.detail_page_artifact_uri and not item.detail_page_artifact_uri.startswith("s3://")
            ):
                raise ValidationError(_("Raw detail-page evidence requires an S3 URI and matching hash."))
            if item.detail_page_artifact_sha256 and (
                item.detail_page_artifact_sha256 != item.detail_title_page_sha256
            ):
                raise ValidationError(_("The raw detail-page artifact hash must match the title evidence page."))

    def _positive_sparex_supplier(self):
        self.ensure_one()
        if not self.matched_product_id:
            return self.env["product.supplierinfo"]
        return (
            self.env["product.supplierinfo"]
            .sudo()
            .search(
                [
                    ("product_tmpl_id", "=", self.matched_product_id.id),
                    ("partner_id.name", "=ilike", "Sparex"),
                    ("price", ">", 0),
                ],
                limit=1,
            )
        )

    def _source_link_snapshot(self):
        self.ensure_one()
        product = self.matched_product_id.sudo()
        before_url = product.southern_source_url or ""
        before_image = product.image_1920 or b""
        if isinstance(before_image, str):
            before_image = before_image.encode("ascii", errors="ignore")
        values = {
            "item_id": self.id,
            "product_id": product.id,
            "sku": self.normalized_sku,
            "source_url_sha256": self.source_url_sha256,
            "source_url": self.source_url,
            "image_url_sha256": self.image_url_sha256 or "",
            "image_url": self.image_url or "",
            "source_artifact_sha256": self.source_artifact_sha256,
            "before_source_url": before_url,
            "before_source_url_sha256": _sha256_text(before_url),
            "before_image_present": bool(product.image_1920),
            "before_image_sha256": hashlib.sha256(before_image).hexdigest(),
            "repair_url": not exact_sparex_url(before_url, self.normalized_sku),
            "repair_image": not bool(product.image_1920),
            "product_write_date": str(product.write_date or ""),
        }
        values["snapshot_sha256"] = _canonical_sha256(values)
        return values

    def _description_repair_snapshot(self):
        """Capture a verified-title replacement for unsafe customer copy."""
        self.ensure_one()
        product = self.matched_product_id.sudo()
        title = _verified_detail_title(self.listing_title)
        if not title:
            raise UserError(_("A verified listing title is required to repair customer copy."))
        safe_title = html.escape(title)
        safe_sku = html.escape(self.normalized_sku)
        ecommerce = (
            f"<p>{safe_title} is available from Southern Equipment under Sparex reference {safe_sku}.</p>"
            "<p>Southern Equipment confirms availability, fitment, and pickup or shipping details before fulfillment.</p>"
        )
        descriptions_before = {
            field_name: product[field_name] or ""
            for field_name in ("description_ecommerce", "website_description", "description_sale")
            if field_name in product._fields
        }
        values = {
            "item_id": self.id,
            "product_id": product.id,
            "sku": self.normalized_sku,
            "listing_title": title,
            "source_url_sha256": self.source_url_sha256,
            "source_artifact_sha256": self.source_artifact_sha256,
            "detail_title_sha256": self.detail_title_sha256 or "",
            "detail_title_page_sha256": self.detail_title_page_sha256 or "",
            "detail_page_artifact_uri": self.detail_page_artifact_uri or "",
            "detail_page_artifact_sha256": self.detail_page_artifact_sha256 or "",
            "descriptions_before": descriptions_before,
            "descriptions_before_sha256": _canonical_sha256(descriptions_before),
            "descriptions_after": {
                "description_ecommerce": ecommerce,
                "website_description": ecommerce,
                "description_sale": (
                    f"{html.unescape(safe_title)}. Sparex reference {html.unescape(safe_sku)}. "
                    "Confirm availability and fitment with Southern Equipment."
                ),
            },
            "product_write_date": str(product.write_date or ""),
        }
        values["snapshot_sha256"] = _canonical_sha256(values)
        return values

    def _refresh_readiness(self):
        now = fields.Datetime.now()
        for item in self:
            product = item.matched_product_id.sudo()
            supplier = item._positive_sparex_supplier()
            supplier_cost = float(supplier.price or 0.0) if supplier else 0.0
            standard_cost = float(product.standard_price or 0.0) if product else 0.0
            has_cost = bool(
                product
                and supplier
                and standard_cost > 0
                and abs(standard_cost - supplier_cost) <= 0.000001
                and exact_dealer_cost_evidence_ready(product)
            )
            has_sales_price = bool(
                product
                and not sales_price_blocker(product, supplier)
                and not pricing_basis_blockers(product, supplier)
            )
            product_has_exact_url = bool(product and exact_sparex_url(product.southern_source_url, item.normalized_sku))
            product_has_image = bool(product and product.image_1920)
            product_has_category = bool(product and product.public_categ_ids)
            product_has_description = bool(product and customer_description_ready(product))
            currently_published = bool(product and product.website_published)
            source_ready = bool(
                item.reconciliation_state == "current"
                and item.state == "verified"
                and item.source_state == "verified"
                and item.odoo_match_state == "matched_active"
                and item.has_exact_sparex_url
                and item.has_image
            )
            enrichment = bool(
                source_ready
                and not currently_published
                and (not product_has_exact_url or not product_has_image)
            )
            primary_blocker = _primary_publication_blocker(
                reconciliation_state=item.reconciliation_state,
                item_state=item.state,
                source_state=item.source_state,
                match_state=item.odoo_match_state,
                product_active=bool(product and product.active),
                currently_published=currently_published,
                has_cost=has_cost,
                has_sales_price=has_sales_price,
                product_has_exact_url=product_has_exact_url,
                product_has_image=product_has_image,
                product_has_category=product_has_category,
                product_has_description=product_has_description,
            )
            recovery_priority = 0
            recovery_values = {}
            needs_detail_title = bool(
                product
                and not product_has_description
                and not _verified_detail_title(item.listing_title)
            )
            if has_cost and not needs_detail_title:
                recovery_values = {
                    "cost_recovery_state": "resolved",
                    "cost_recovery_priority": 0,
                    "cost_recovery_next_at": False,
                    "cost_recovery_worker_id": False,
                    "cost_recovery_last_error": False,
                }
            elif (
                (not has_cost or needs_detail_title)
                and item.reconciliation_state == "current"
                and item.source_state in {"verified", "missing_image"}
                and item.odoo_match_state == "matched_active"
                and bool(product and product.active)
                and not currently_published
                and product_has_exact_url
            ):
                recovery_priority = 100 if not has_cost else 80
                recovery_priority += 30 if has_sales_price else 0
                recovery_priority += 20 if product_has_exact_url else 0
                recovery_priority += 10 if product_has_image else 0
                claimed_until = (
                    item.cost_recovery_claimed_at + timedelta(minutes=30) if item.cost_recovery_claimed_at else False
                )
                if item.cost_recovery_state == "manual_review":
                    next_state = "manual_review"
                elif item.cost_recovery_state == "claimed" and claimed_until and claimed_until > now:
                    next_state = "claimed"
                elif item.cost_recovery_attempt_count >= MAX_COST_RECOVERY_ATTEMPTS:
                    next_state = "manual_review"
                elif (
                    item.cost_recovery_state == "retry_wait"
                    and item.cost_recovery_next_at
                    and item.cost_recovery_next_at > now
                ):
                    next_state = "retry_wait"
                else:
                    next_state = "queued"
                recovery_values = {
                    "cost_recovery_state": next_state,
                    "cost_recovery_priority": recovery_priority,
                }
                if next_state == "queued":
                    recovery_values.update(
                        {
                            "cost_recovery_next_at": False,
                            "cost_recovery_worker_id": False,
                        }
                    )
            else:
                recovery_values = {
                    "cost_recovery_state": "not_required",
                    "cost_recovery_priority": 0,
                    "cost_recovery_next_at": False,
                    "cost_recovery_worker_id": False,
                    "cost_recovery_last_error": False,
                }
            item.write(
                {
                    "has_positive_supplier_cost": has_cost,
                    "has_positive_sales_price": has_sales_price,
                    "product_has_exact_sparex_url": product_has_exact_url,
                    "product_has_image": product_has_image,
                    "currently_published": currently_published,
                    "source_enrichment_candidate": enrichment,
                    "primary_blocker": primary_blocker,
                    "readiness_refreshed_at": now,
                    "publication_candidate": bool(
                        source_ready
                        and not currently_published
                        and has_cost
                        and has_sales_price
                        and product_has_exact_url
                        and product_has_image
                        and product_has_category
                        and product_has_description
                    ),
                    **recovery_values,
                }
            )
        return True

    @api.model
    def refresh_readiness_batch(self, limit=500):
        bounded = max(1, min(int(limit or 500), 2000))
        items = self.search(
            [("company_id", "=", self.env.company.id), ("reconciliation_state", "=", "current")],
            order="readiness_refreshed_at, id",
            limit=bounded,
        )
        items._refresh_readiness()
        counts = {}
        for blocker in items.mapped("primary_blocker"):
            counts[blocker or "unclassified"] = counts.get(blocker or "unclassified", 0) + 1
        recovery_counts = {}
        for state in items.mapped("cost_recovery_state"):
            recovery_counts[state or "unclassified"] = recovery_counts.get(state or "unclassified", 0) + 1
        return {"refreshed": len(items), "blockers": counts, "cost_recovery": recovery_counts}

    @api.model
    def continuous_release_status(self):
        """Return an Odoo-only gate for the next bounded release dispatch."""
        now = fields.Datetime.now()
        refresh_items = self.search(
            [
                ("company_id", "=", self.env.company.id),
                ("reconciliation_state", "=", "current"),
                ("odoo_match_state", "=", "matched_active"),
            ],
            order="readiness_refreshed_at, id",
            limit=500,
        )
        refresh_items._refresh_readiness()
        self.flush_model(
            [
                "publication_candidate",
                "source_enrichment_candidate",
                "cost_recovery_state",
                "cost_recovery_next_at",
            ]
        )
        self.env["product.template"].flush_model(["is_published"])
        self.env.cr.execute(
            """
            WITH classified AS (
                SELECT CASE
                         WHEN item.publication_candidate IS TRUE
                           OR item.source_enrichment_candidate IS TRUE
                           OR item.cost_recovery_state = 'queued'
                           OR (
                                item.cost_recovery_state = 'retry_wait'
                                AND (item.cost_recovery_next_at IS NULL OR item.cost_recovery_next_at <= %s)
                              )
                           THEN 'actionable'
                         WHEN item.cost_recovery_state = 'claimed'
                           OR (item.cost_recovery_state = 'retry_wait' AND item.cost_recovery_next_at > %s)
                           THEN 'waiting'
                         WHEN item.cost_recovery_state = 'manual_review'
                           THEN 'manual'
                         ELSE 'blocked'
                       END AS bucket,
                       item.cost_recovery_next_at
                  FROM southern_sparex_discovery_item item
                  JOIN product_template product
                    ON product.id = item.matched_product_id
                   AND product.is_published IS FALSE
                 WHERE item.company_id = %s
                   AND item.reconciliation_state = 'current'
                   AND item.odoo_match_state = 'matched_active'
            )
            SELECT COUNT(*) FILTER (WHERE bucket = 'actionable'),
                   COUNT(*) FILTER (WHERE bucket = 'waiting'),
                   COUNT(*) FILTER (WHERE bucket = 'manual'),
                   COUNT(*) FILTER (WHERE bucket = 'blocked'),
                   MIN(cost_recovery_next_at) FILTER (WHERE bucket = 'waiting')
              FROM classified
            """,
            [now, now, self.env.company.id],
        )
        actionable_count, waiting_count, manual_count, blocked_count, next_attempt_at = self.env.cr.fetchone()
        if actionable_count:
            state = "actionable"
        elif waiting_count:
            state = "waiting"
        else:
            unlinked_count = self.search_count(
                [
                    ("company_id", "=", self.env.company.id),
                    ("reconciliation_state", "=", "current"),
                    ("odoo_match_state", "!=", "matched_active"),
                ]
            )
            self.env["product.template"].flush_model(["active", "default_code"])
            self.env.cr.execute(
                """
                SELECT COUNT(*)
                  FROM product_template product
                 WHERE product.active IS TRUE
                   AND product.default_code LIKE 'S.%%'
                   AND NOT EXISTS (
                        SELECT 1
                          FROM southern_sparex_discovery_item item
                         WHERE item.company_id = %s
                           AND item.reconciliation_state = 'current'
                           AND item.matched_product_id = product.id
                   )
                """,
                [self.env.company.id],
            )
            untracked_product_count = self.env.cr.fetchone()[0]
            state = (
                "needs_review"
                if manual_count or blocked_count or unlinked_count or untracked_product_count
                else "complete"
            )
            return {
                "state": state,
                "actionable_count": 0,
                "waiting_count": 0,
                "manual_review_count": manual_count,
                "blocked_count": blocked_count,
                "unlinked_count": unlinked_count,
                "untracked_product_count": untracked_product_count,
                "next_attempt_at": False,
            }
        return {
            "state": state,
            "actionable_count": actionable_count,
            "waiting_count": waiting_count,
            "manual_review_count": manual_count,
            "blocked_count": blocked_count,
            "unlinked_count": 0,
            "untracked_product_count": 0,
            "next_attempt_at": next_attempt_at or False,
        }

    @api.model
    def claim_cost_recovery_batch(self, worker_id, limit=MAX_COST_RECOVERY_BATCH):
        worker = (worker_id or "").strip()
        if not worker:
            raise UserError(_("Cost recovery requires a worker identifier."))
        bounded = max(1, min(int(limit or MAX_COST_RECOVERY_BATCH), MAX_COST_RECOVERY_BATCH))
        now = fields.Datetime.now()
        self.env.cr.execute(
            """
            SELECT id FROM southern_sparex_discovery_item
             WHERE company_id = %s
               AND reconciliation_state = 'current'
               AND cost_recovery_state IN ('queued', 'retry_wait')
               AND (cost_recovery_next_at IS NULL OR cost_recovery_next_at <= %s)
             ORDER BY cost_recovery_priority DESC, readiness_refreshed_at, id
             FOR UPDATE SKIP LOCKED LIMIT %s
            """,
            [self.env.company.id, now, bounded],
        )
        items = self.browse([row[0] for row in self.env.cr.fetchall()])
        items.write(
            {
                "cost_recovery_state": "claimed",
                "cost_recovery_claimed_at": now,
                "cost_recovery_worker_id": worker,
            }
        )
        for item in items:
            item.cost_recovery_attempt_count += 1
        claimed = []
        for item in items:
            product = item.matched_product_id.sudo()
            supplier_lines = (
                self.env["product.supplierinfo"]
                .sudo()
                .search(
                    [
                        ("product_tmpl_id", "=", product.id),
                        ("partner_id.name", "=ilike", "Sparex"),
                    ],
                    limit=2,
                )
            )
            supplier = supplier_lines if len(supplier_lines) == 1 else self.env["product.supplierinfo"]
            snapshot = {
                "item_id": item.id,
                "product_id": product.id,
                "sku": item.normalized_sku,
                "source_url": item.source_url,
                "source_url_sha256": item.source_url_sha256,
                "source_artifact_sha256": item.source_artifact_sha256,
                "priority": item.cost_recovery_priority,
                "attempt": item.cost_recovery_attempt_count,
                "has_sales_price": item.has_positive_sales_price,
                "has_exact_product_url": item.product_has_exact_sparex_url,
                "has_image": item.product_has_image,
                "supplierinfo_count": len(supplier_lines),
                "supplierinfo_id": supplier.id if supplier else None,
                "supplier_price_before": supplier.price if supplier else None,
                "supplier_write_date": str(supplier.write_date or "") if supplier else "",
                "standard_price_before": product.standard_price,
                "list_price_before": product.list_price,
                "quote_only_before": bool(product.southern_quote_only),
                "price_basis_before": product.southern_price_basis,
                "cost_plus_margin_before": product.southern_cost_plus_margin_percent,
                "price_basis_updated_at_before": str(product.southern_price_basis_updated_at or ""),
                "product_write_date": str(product.write_date or ""),
                "listing_title_before": item.listing_title or "",
                "detail_title_sha256_before": item.detail_title_sha256 or "",
                "detail_title_page_sha256_before": item.detail_title_page_sha256 or "",
                "detail_title_parser_version_before": item.detail_title_parser_version or "",
                "detail_title_recovered_at_before": str(item.detail_title_recovered_at or ""),
                "detail_page_artifact_uri_before": item.detail_page_artifact_uri or "",
                "detail_page_artifact_sha256_before": item.detail_page_artifact_sha256 or "",
            }
            snapshot["snapshot_sha256"] = _canonical_sha256(snapshot)
            claimed.append(snapshot)
        return claimed

    @api.model
    def apply_cost_recovery_plan(self, records, worker_id, confirmation, reason):
        worker = (worker_id or "").strip()
        if confirmation != COST_RECOVERY_CONFIRMATION or not (reason or "").strip():
            raise UserError(_("Dealer-cost recovery requires explicit confirmation and a business reason."))
        if not worker or len(records or []) > MAX_COST_RECOVERY_BATCH:
            raise UserError(_("Dealer-cost recovery worker or batch limit is invalid."))
        applied = []
        for record in records or []:
            item = self.browse(int(record.get("item_id") or 0)).exists()
            if not item or item.company_id != self.env.company:
                raise UserError(_("The dealer-cost recovery item is unavailable."))
            self.env.cr.execute(
                "SELECT id FROM southern_sparex_discovery_item WHERE id = %s FOR UPDATE NOWAIT",
                [item.id],
            )
            item.invalidate_recordset()
            if item.cost_recovery_state != "claimed" or item.cost_recovery_worker_id != worker:
                raise UserError(_("The dealer-cost recovery claim is no longer owned by this worker."))
            product = item.matched_product_id.sudo()
            if (
                not product
                or not product.active
                or product.id != int(record.get("product_id") or 0)
                or item.normalized_sku != normalized_sparex_sku(record.get("sku"))
                or not exact_sparex_url(item.source_url, item.normalized_sku)
                or item.source_url != (record.get("evidence_url") or "").strip()
                or item.source_url_sha256 != (record.get("evidence_url_sha256") or "").casefold()
            ):
                raise UserError(_("Dealer-cost evidence does not match the exact active product and SKU."))
            supplier_lines = (
                self.env["product.supplierinfo"]
                .sudo()
                .search(
                    [
                        ("product_tmpl_id", "=", product.id),
                        ("partner_id.name", "=ilike", "Sparex"),
                    ],
                    limit=2,
                )
            )
            if len(supplier_lines) != 1 or supplier_lines.id != int(record.get("supplierinfo_id") or 0):
                raise UserError(_("Exactly one existing matching Sparex supplier line is required."))
            expected_snapshot = {
                key: record.get(key)
                for key in (
                    "item_id",
                    "product_id",
                    "sku",
                    "source_url",
                    "source_url_sha256",
                    "source_artifact_sha256",
                    "priority",
                    "attempt",
                    "has_sales_price",
                    "has_exact_product_url",
                    "has_image",
                    "supplierinfo_count",
                    "supplierinfo_id",
                    "supplier_price_before",
                    "supplier_write_date",
                    "standard_price_before",
                    "list_price_before",
                    "quote_only_before",
                    "price_basis_before",
                    "cost_plus_margin_before",
                    "price_basis_updated_at_before",
                    "product_write_date",
                    "listing_title_before",
                    "detail_title_sha256_before",
                    "detail_title_page_sha256_before",
                    "detail_title_parser_version_before",
                    "detail_title_recovered_at_before",
                    "detail_page_artifact_uri_before",
                    "detail_page_artifact_sha256_before",
                )
            }
            if _canonical_sha256(expected_snapshot) != (record.get("snapshot_sha256") or "").casefold():
                raise UserError(_("Dealer-cost rollback snapshot hash does not match."))
            if abs(float(supplier_lines.price) - float(record.get("supplier_price_before") or 0.0)) > 1e-9:
                raise UserError(_("The Sparex supplier price changed after the rollback snapshot."))
            price = float(record.get("dealer_price") or 0.0)
            if price <= 0 or (record.get("currency") or "").upper() != "USD":
                raise UserError(_("A positive exact USD dealer price is required."))
            evidence_sha = (record.get("evidence_sha256") or "").casefold()
            if not SHA256_PATTERN.fullmatch(evidence_sha):
                raise UserError(_("Dealer-cost page evidence requires a SHA-256 hash."))
            detail_title = _verified_detail_title(record.get("detail_title"))
            detail_title_sha = (record.get("detail_title_sha256") or "").casefold()
            detail_title_page_sha = (record.get("detail_title_page_sha256") or "").casefold()
            detail_page_artifact_uri = (record.get("detail_page_artifact_uri") or "").strip()
            detail_page_artifact_sha = (record.get("detail_page_artifact_sha256") or "").casefold()
            if (
                not detail_title
                or detail_title_sha != _sha256_text(detail_title)
                or detail_title_page_sha != evidence_sha
                or not detail_page_artifact_uri.startswith("s3://")
                or detail_page_artifact_sha != evidence_sha
            ):
                raise UserError(_("The exact detail-page title or its evidence hash is invalid."))
            image_applied = False
            image_sha = (record.get("detail_image_sha256") or "").casefold()
            image_content = record.get("detail_image_base64") or ""
            image_url = (record.get("detail_image_url") or "").strip()
            if image_content or image_sha or image_url:
                if product.image_1920 or record.get("has_image"):
                    raise UserError(_("Detail-page image recovery is allowed only when the product snapshot had no image."))
                if not _https_url(image_url) or _sha256_text(image_url) != (
                    record.get("detail_image_url_sha256") or ""
                ).casefold():
                    raise UserError(_("The recovered detail-page image URL is invalid."))
                if not SHA256_PATTERN.fullmatch(image_sha):
                    raise UserError(_("The recovered detail-page image requires a SHA-256 hash."))
                try:
                    decoded_image = base64.b64decode(image_content, validate=True)
                except (ValueError, TypeError) as exc:
                    raise UserError(_("The recovered detail-page image is not valid base64.")) from exc
                if not decoded_image or len(decoded_image) > 10 * 1024 * 1024 or hashlib.sha256(decoded_image).hexdigest() != image_sha:
                    raise UserError(_("The recovered detail-page image content does not match its evidence hash."))
                product.write({"image_1920": image_content})
                product.invalidate_recordset(["image_1920"])
                if not product.image_1920:
                    raise UserError(_("The recovered Sparex product image did not verify."))
                image_applied = True
                stored_image = product.image_1920
                if isinstance(stored_image, str):
                    stored_image = stored_image.encode("ascii", errors="ignore")
                stored_image_sha = hashlib.sha256(stored_image).hexdigest()
            supplier_lines.write({"price": price})
            supplier_lines.invalidate_recordset(["price"])
            if abs(float(supplier_lines.price) - price) > 1e-9:
                raise UserError(_("The recovered Sparex supplier price did not verify."))
            margin_parameter = self.env["ir.config_parameter"].sudo().get_param(
                "southern_parts_intelligence.provisional_cost_plus_margin_percent",
                DEFAULT_COST_PLUS_MARGIN_PERCENT,
            )
            try:
                margin_percent = float(margin_parameter)
            except (TypeError, ValueError):
                margin_percent = DEFAULT_COST_PLUS_MARGIN_PERCENT
            if margin_percent <= 0 or margin_percent >= 90:
                raise UserError(_("The provisional cost-plus gross margin must be greater than 0 and less than 90."))
            apply_cost_plus_price = bool(
                product.southern_quote_only
                or product.list_price <= 0
                or product.southern_price_basis == "none"
            )
            provisional_price = math.ceil((price / (1.0 - (margin_percent / 100.0))) * 100.0) / 100.0
            product_values = {"standard_price": price}
            if apply_cost_plus_price:
                product_values.update(
                    {
                        "southern_quote_only": False,
                        "list_price": provisional_price,
                        "southern_price_basis": "cost_plus",
                        "southern_cost_plus_margin_percent": margin_percent,
                        "southern_price_basis_updated_at": fields.Datetime.now(),
                    }
                )
            product.write(product_values)
            product.invalidate_recordset(
                [
                    "standard_price",
                    "list_price",
                    "southern_quote_only",
                    "southern_price_basis",
                    "southern_cost_plus_margin_percent",
                    "southern_price_basis_updated_at",
                ]
            )
            if abs(float(product.standard_price) - price) > 1e-9:
                raise UserError(_("The recovered Sparex standard cost did not verify."))
            if apply_cost_plus_price and (
                product.southern_quote_only
                or abs(float(product.list_price) - provisional_price) > 1e-9
                or product.southern_price_basis != "cost_plus"
            ):
                raise UserError(_("The provisional Sparex cost-plus sales price did not verify."))
            item.write(
                {
                    "listing_title": detail_title,
                    "detail_title_sha256": detail_title_sha,
                    "detail_title_page_sha256": detail_title_page_sha,
                    "detail_title_parser_version": (record.get("parser_version") or "")[:80],
                    "detail_title_recovered_at": fields.Datetime.now(),
                    "detail_page_artifact_uri": detail_page_artifact_uri,
                    "detail_page_artifact_sha256": detail_page_artifact_sha,
                    "cost_recovery_state": "resolved",
                    "cost_recovery_priority": 0,
                    "cost_recovery_next_at": False,
                    "cost_recovery_worker_id": False,
                    "cost_recovery_last_error": False,
                    "cost_evidence_sha256": evidence_sha,
                    "cost_evidence_url_sha256": item.source_url_sha256,
                    "cost_recovery_parser_version": (record.get("parser_version") or "")[:80],
                    "cost_recovered_at": fields.Datetime.now(),
                }
            )
            item._refresh_readiness()
            applied.append(
                {
                    "item_id": item.id,
                    "product_id": product.id,
                    "sku": item.normalized_sku,
                    "supplierinfo_id": supplier_lines.id,
                    "supplier_price_before": float(record.get("supplier_price_before") or 0.0),
                    "supplier_price_applied": price,
                    "standard_price_before": float(record.get("standard_price_before") or 0.0),
                    "standard_price_applied": price,
                    "list_price_before": float(record.get("list_price_before") or 0.0),
                    "list_price_applied": float(product.list_price),
                    "quote_only_before": bool(record.get("quote_only_before")),
                    "quote_only_applied": bool(product.southern_quote_only),
                    "price_basis_before": record.get("price_basis_before") or "none",
                    "price_basis_applied": product.southern_price_basis,
                    "cost_plus_margin_before": float(record.get("cost_plus_margin_before") or 0.0),
                    "cost_plus_margin_applied": float(product.southern_cost_plus_margin_percent or 0.0),
                    "price_basis_updated_at_before": record.get("price_basis_updated_at_before") or "",
                    "cost_plus_price_applied": apply_cost_plus_price,
                    "image_applied": image_applied,
                    "image_source_sha256": image_sha if image_applied else "",
                    "image_sha256_applied": stored_image_sha if image_applied else "",
                    "evidence_sha256": evidence_sha,
                    "evidence_url_sha256": item.source_url_sha256,
                    "detail_title_applied": detail_title,
                    "detail_title_sha256_applied": detail_title_sha,
                    "detail_title_page_sha256_applied": detail_title_page_sha,
                    "detail_page_artifact_uri_applied": detail_page_artifact_uri,
                    "detail_page_artifact_sha256_applied": detail_page_artifact_sha,
                    "listing_title_before": record.get("listing_title_before") or "",
                    "detail_title_sha256_before": record.get("detail_title_sha256_before") or "",
                    "detail_title_page_sha256_before": record.get("detail_title_page_sha256_before") or "",
                    "detail_title_parser_version_before": record.get("detail_title_parser_version_before") or "",
                    "detail_title_recovered_at_before": record.get("detail_title_recovered_at_before") or "",
                    "detail_page_artifact_uri_before": record.get("detail_page_artifact_uri_before") or "",
                    "detail_page_artifact_sha256_before": record.get("detail_page_artifact_sha256_before") or "",
                    "publication_candidate": item.publication_candidate,
                }
            )
        return applied

    @api.model
    def rollback_cost_recovery(self, records, reason):
        if not (reason or "").strip() or len(records or []) > MAX_COST_RECOVERY_BATCH:
            raise UserError(_("A bounded dealer-cost rollback reason is required."))
        rolled_back = []
        for record in records or []:
            item = self.browse(int(record.get("item_id") or 0)).exists()
            supplier = self.env["product.supplierinfo"].sudo().browse(int(record.get("supplierinfo_id") or 0)).exists()
            if (
                not item
                or item.company_id != self.env.company
                or not supplier
                or supplier.product_tmpl_id != item.matched_product_id
                or supplier.partner_id.name.casefold() != "sparex"
                or abs(float(supplier.price) - float(record.get("supplier_price_applied") or 0.0)) > 1e-9
            ):
                raise UserError(_("Dealer-cost rollback scope or current value does not match."))
            product = item.matched_product_id.sudo()
            if (
                abs(float(product.standard_price) - float(record.get("standard_price_applied") or 0.0)) > 1e-9
                or abs(float(product.list_price) - float(record.get("list_price_applied") or 0.0)) > 1e-9
                or bool(product.southern_quote_only) != bool(record.get("quote_only_applied"))
                or product.southern_price_basis != (record.get("price_basis_applied") or "none")
            ):
                raise UserError(_("Dealer-cost rollback product values no longer match the applied snapshot."))
            if record.get("image_applied"):
                current_image_sha = hashlib.sha256(base64.b64decode(product.image_1920 or b"")).hexdigest()
                if current_image_sha != (record.get("image_sha256_applied") or "").casefold():
                    raise UserError(_("Dealer-cost rollback image no longer matches the applied snapshot."))
            product.write(
                {
                    "standard_price": float(record.get("standard_price_before") or 0.0),
                    "list_price": float(record.get("list_price_before") or 0.0),
                    "southern_quote_only": bool(record.get("quote_only_before")),
                    "southern_price_basis": record.get("price_basis_before") or "none",
                    "southern_cost_plus_margin_percent": float(record.get("cost_plus_margin_before") or 0.0),
                    "southern_price_basis_updated_at": record.get("price_basis_updated_at_before") or False,
                    **({"image_1920": False} if record.get("image_applied") else {}),
                }
            )
            supplier.write({"price": float(record.get("supplier_price_before") or 0.0)})
            item.write(
                {
                    "listing_title": record.get("listing_title_before") or False,
                    "detail_title_sha256": record.get("detail_title_sha256_before") or False,
                    "detail_title_page_sha256": record.get("detail_title_page_sha256_before") or False,
                    "detail_title_parser_version": record.get("detail_title_parser_version_before") or False,
                    "detail_title_recovered_at": record.get("detail_title_recovered_at_before") or False,
                    "detail_page_artifact_uri": record.get("detail_page_artifact_uri_before") or False,
                    "detail_page_artifact_sha256": record.get("detail_page_artifact_sha256_before") or False,
                    "cost_recovery_state": "manual_review",
                    "cost_recovery_worker_id": False,
                    "cost_recovery_next_at": False,
                    "cost_recovery_last_error": (reason or "rollback")[:120],
                }
            )
            item._refresh_readiness()
            rolled_back.append(item.id)
        return rolled_back

    @api.model
    def record_cost_recovery_result(self, item_id, worker_id, outcome, error_code=""):
        allowed = {"cost_present", "not_found", "source_unavailable", "manual_review"}
        if outcome not in allowed:
            raise UserError(_("Invalid cost-recovery outcome."))
        item = self.browse(int(item_id)).exists()
        if not item or item.company_id != self.env.company:
            raise UserError(_("The cost-recovery item does not exist in the active company."))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_item WHERE id = %s FOR UPDATE NOWAIT", [item.id])
        item.invalidate_recordset()
        if item.cost_recovery_state != "claimed" or item.cost_recovery_worker_id != (worker_id or "").strip():
            raise UserError(_("The cost-recovery claim is no longer owned by this worker."))
        if outcome == "cost_present":
            item._refresh_readiness()
            if not item.has_positive_supplier_cost:
                raise UserError(_("Dealer cost is still absent; verified evidence must be applied separately."))
            return True
        if outcome == "manual_review" or item.cost_recovery_attempt_count >= MAX_COST_RECOVERY_ATTEMPTS:
            values = {
                "cost_recovery_state": "manual_review",
                "cost_recovery_next_at": False,
            }
        else:
            delay_minutes = min(24 * 60, 15 * (2 ** max(0, item.cost_recovery_attempt_count - 1)))
            if (error_code or "").startswith(("portal_", "dealer_login", "dealer_session")):
                delay_minutes = max(60, delay_minutes)
            values = {
                "cost_recovery_state": "retry_wait",
                "cost_recovery_next_at": fields.Datetime.now() + timedelta(minutes=delay_minutes),
            }
        values.update(
            {
                "cost_recovery_worker_id": False,
                "cost_recovery_last_error": (error_code or outcome)[:120],
            }
        )
        item.write(values)
        return True

    @api.model
    def record_durable_cost_staged(self, item_id, worker_id, catalog_item_id):
        item = self.browse(int(item_id)).exists()
        catalog_item = self.env["southern.vendor.catalog.item"].sudo().browse(int(catalog_item_id)).exists()
        if not item or not catalog_item or item.company_id != self.env.company:
            raise UserError(_("The durable dealer-cost claim or staging record is unavailable."))
        self.env.cr.execute("SELECT id FROM southern_sparex_discovery_item WHERE id = %s FOR UPDATE NOWAIT", [item.id])
        item.invalidate_recordset()
        if item.cost_recovery_state != "claimed" or item.cost_recovery_worker_id != (worker_id or "").strip():
            raise UserError(_("The durable dealer-cost claim is no longer owned by this worker."))
        if (
            item.normalized_sku != catalog_item.normalized_sku
            or catalog_item.vendor_cost <= 0
            or not catalog_item.dealer_cost_evidence_sha256
        ):
            raise UserError(_("Durable dealer-cost staging did not verify the exact SKU and evidence."))
        item._link_durable_cost_evidence(catalog_item)
        return True

    def _link_durable_cost_evidence(self, catalog_item):
        self.ensure_one()
        evidence_sha = (catalog_item.dealer_cost_evidence_sha256 or "").casefold()
        source_url = (catalog_item.source_url or "").strip()
        if (
            catalog_item.source_id.code != "sparex"
            or self.normalized_sku != catalog_item.normalized_sku
            or catalog_item.vendor_cost <= 0
            or not SHA256_PATTERN.fullmatch(evidence_sha)
            or not catalog_item.dealer_cost_observed_at
            or source_url != (self.source_url or "").strip()
            or _sha256_text(source_url) != (self.source_url_sha256 or "").casefold()
        ):
            raise UserError(_("Durable dealer-cost evidence does not match the discovery identity."))
        self.write(
            {
                "cost_recovery_state": "resolved",
                "cost_recovery_worker_id": False,
                "cost_recovery_next_at": False,
                "cost_recovery_last_error": False,
                "cost_evidence_sha256": evidence_sha,
                "cost_evidence_url_sha256": self.source_url_sha256,
                "cost_recovery_parser_version": (catalog_item.schema_version or "")[:80],
                "cost_recovered_at": catalog_item.dealer_cost_observed_at,
            }
        )
        self._refresh_readiness()
        return True

    @api.model
    def backfill_durable_cost_evidence_links(self, limit=500):
        bounded = max(1, min(int(limit or 500), 500))
        from .sparex_manifest import acquire_sparex_catalog_lock

        acquire_sparex_catalog_lock(self.env)
        items = self.sudo().search(
            [
                ("company_id", "=", self.env.company.id),
                ("reconciliation_state", "=", "current"),
                ("cost_recovery_state", "=", "resolved"),
                ("cost_evidence_sha256", "=", False),
                ("matched_product_id", "!=", False),
            ],
            order="id",
            limit=bounded,
        )
        source = self.env["southern.vendor.catalog.source"].sudo().search(
            [("company_id", "=", self.env.company.id), ("code", "=", "sparex")],
            limit=1,
        )
        catalog_items = self.env["southern.vendor.catalog.item"].sudo().search(
            [
                ("source_id", "=", source.id),
                ("normalized_sku", "in", items.mapped("normalized_sku")),
                ("vendor_cost", ">", 0),
                ("dealer_cost_evidence_sha256", "!=", False),
                ("dealer_cost_observed_at", "!=", False),
            ]
        )
        by_sku = {catalog_item.normalized_sku: catalog_item for catalog_item in catalog_items}
        linked = []
        skipped = []
        for item in items:
            catalog_item = by_sku.get(item.normalized_sku)
            if not catalog_item or (catalog_item.source_url or "").strip() != (item.source_url or "").strip():
                skipped.append(item.id)
                continue
            item._link_durable_cost_evidence(catalog_item)
            linked.append(item.id)
        return {"linked_item_ids": linked, "skipped_item_ids": skipped}

    @api.model
    def prepare_source_link_plan(self, limit=MAX_SOURCE_LINK_BATCH):
        bounded = max(1, min(int(limit or MAX_SOURCE_LINK_BATCH), MAX_SOURCE_LINK_BATCH))
        candidates = self.search(
            [
                ("reconciliation_state", "=", "current"),
                ("state", "=", "verified"),
                ("source_state", "=", "verified"),
                ("odoo_match_state", "=", "matched_active"),
                ("matched_product_id.website_published", "=", False),
                ("has_exact_sparex_url", "=", True),
                ("has_image", "=", True),
                ("source_enrichment_candidate", "=", True),
            ],
            order="last_seen_at, id",
            limit=bounded * 4,
        )
        prepared = []
        for item in candidates:
            item._refresh_readiness()
            product = item.matched_product_id.sudo()
            if (
                not item.source_enrichment_candidate
                or not product
                or not product.active
            ):
                continue
            prepared.append(item._source_link_snapshot())
            if len(prepared) >= bounded:
                break
        return prepared

    @api.model
    def prepare_description_repair_plan(self, limit=MAX_SOURCE_LINK_BATCH):
        """Plan bounded repair of placeholder or contaminated customer copy."""
        bounded = max(1, min(int(limit or MAX_SOURCE_LINK_BATCH), MAX_SOURCE_LINK_BATCH))
        candidates = self.search(
            [
                ("reconciliation_state", "=", "current"),
                ("state", "=", "verified"),
                ("source_state", "=", "verified"),
                ("odoo_match_state", "=", "matched_active"),
                ("has_exact_sparex_url", "=", True),
                ("has_image", "=", True),
                ("listing_title", "!=", False),
                ("primary_blocker", "in", ("missing_customer_description", "already_published")),
            ],
            order="readiness_refreshed_at, id",
            limit=bounded * 4,
        )
        prepared = []
        for item in candidates:
            item._refresh_readiness()
            product = item.matched_product_id.sudo()
            if (
                not product
                or not product.active
                or not product.public_categ_ids
                or not _verified_detail_title(item.listing_title)
                or customer_description_ready(product)
            ):
                continue
            prepared.append(item._description_repair_snapshot())
            if len(prepared) >= bounded:
                break
        return prepared

    @api.model
    def apply_description_repair_plan(self, records, confirmation, reason):
        if confirmation != DESCRIPTION_REPAIR_CONFIRMATION or not (reason or "").strip():
            raise UserError(_("Description repair requires the exact confirmation and business reason."))
        if not records or len(records) > MAX_SOURCE_LINK_BATCH:
            raise UserError(_("Description-repair batches must contain between 1 and 50 records."))
        applied = []
        for prepared in records:
            item = self.browse(int(prepared.get("item_id") or 0)).exists()
            if not item:
                raise UserError(_("A prepared discovery item no longer exists."))
            self.env.cr.execute(
                "SELECT id FROM southern_sparex_discovery_item WHERE id = %s FOR UPDATE NOWAIT", [item.id]
            )
            item.invalidate_recordset()
            item._refresh_readiness()
            product = item.matched_product_id.sudo()
            self.env.cr.execute("SELECT id FROM product_template WHERE id = %s FOR UPDATE NOWAIT", [product.id])
            product.invalidate_recordset()
            if (
                product.id != int(prepared.get("product_id") or 0)
                or item.normalized_sku != normalized_sparex_sku(prepared.get("sku"))
                or item.source_url_sha256 != prepared.get("source_url_sha256")
                or item._description_repair_snapshot().get("snapshot_sha256") != prepared.get("snapshot_sha256")
            ):
                raise UserError(_("Description evidence changed; create a fresh plan."))
            product.write(prepared.get("descriptions_after") or {})
            product.invalidate_recordset(["description_ecommerce", "website_description", "description_sale"])
            if not customer_description_ready(product):
                product.write(prepared.get("descriptions_before") or {})
                raise UserError(_("The customer description was not retained; the prior values were restored."))
            item._refresh_readiness()
            applied.append(
                {
                    **prepared,
                    "descriptions_after_sha256": _canonical_sha256(
                        {
                            field_name: product[field_name] or ""
                            for field_name in ("description_ecommerce", "website_description", "description_sale")
                            if field_name in product._fields
                        }
                    ),
                }
            )
        return applied

    @api.model
    def rollback_description_repairs(self, records, reason):
        if not (reason or "").strip():
            raise UserError(_("Description-repair rollback requires a reason."))
        for prepared in records or []:
            item = self.browse(int(prepared.get("item_id") or 0)).exists()
            if not item or item.normalized_sku != normalized_sparex_sku(prepared.get("sku")):
                continue
            product = item.matched_product_id.sudo()
            if product.id != int(prepared.get("product_id") or 0):
                continue
            self.env.cr.execute("SELECT id FROM product_template WHERE id = %s FOR UPDATE NOWAIT", [product.id])
            product.invalidate_recordset()
            descriptions_after = {
                field_name: product[field_name] or ""
                for field_name in ("description_ecommerce", "website_description", "description_sale")
                if field_name in product._fields
            }
            if _canonical_sha256(descriptions_after) != (prepared.get("descriptions_after_sha256") or ""):
                raise UserError(_("Description rollback stopped because the product copy changed after this run."))
            product.write(prepared.get("descriptions_before") or {})
            item._refresh_readiness()
        return True

    @api.model
    def apply_source_link_plan(self, records, confirmation, reason):
        if confirmation != SOURCE_LINK_CONFIRMATION or not (reason or "").strip():
            raise UserError(_("Source linking requires the exact confirmation and business reason."))
        if not records or len(records) > MAX_SOURCE_LINK_BATCH:
            raise UserError(_("Source-link batches must contain between 1 and 50 records."))
        applied = []
        for prepared in records:
            item = self.browse(int(prepared.get("item_id") or 0)).exists()
            if not item:
                raise UserError(_("A prepared discovery item no longer exists."))
            self.env.cr.execute(
                "SELECT id FROM southern_sparex_discovery_item WHERE id = %s FOR UPDATE NOWAIT", [item.id]
            )
            item.invalidate_recordset()
            item._refresh_readiness()
            product = item.matched_product_id.sudo()
            self.env.cr.execute("SELECT id FROM product_template WHERE id = %s FOR UPDATE NOWAIT", [product.id])
            product.invalidate_recordset()
            if (
                not item.source_enrichment_candidate
                or product.id != int(prepared.get("product_id") or 0)
                or item.normalized_sku != normalized_sparex_sku(prepared.get("sku"))
                or item.source_url_sha256 != prepared.get("source_url_sha256")
                or item._source_link_snapshot().get("snapshot_sha256") != prepared.get("snapshot_sha256")
            ):
                raise UserError(_("Source-link evidence changed; create a fresh plan."))
            repair_url = bool(prepared.get("repair_url"))
            repair_image = bool(prepared.get("repair_image"))
            if not repair_url and not repair_image:
                raise UserError(_("The prepared source repair no longer has a missing product field."))
            write_values = {}
            if repair_url:
                write_values["southern_source_url"] = item.source_url
            image_content_sha = ""
            if repair_image:
                encoded_image = (prepared.get("image_base64") or "").strip()
                try:
                    image_content = base64.b64decode(encoded_image, validate=True)
                except (TypeError, ValueError) as exc:
                    raise UserError(_("The prepared listing image is not valid base64 evidence.")) from exc
                image_content_sha = hashlib.sha256(image_content).hexdigest()
                if (
                    not image_content
                    or len(image_content) > 10 * 1024 * 1024
                    or image_content_sha != (prepared.get("image_content_sha256") or "").casefold()
                ):
                    raise UserError(_("The prepared listing image hash or size is invalid."))
                write_values["image_1920"] = encoded_image
            product.write(write_values)
            product.invalidate_recordset(["southern_source_url", "image_1920"])
            if not exact_sparex_url(product.southern_source_url, item.normalized_sku):
                product.write(
                    {
                        "southern_source_url": prepared.get("before_source_url") or False,
                        **({"image_1920": False} if repair_image else {}),
                    }
                )
                raise UserError(_("The exact Sparex URL was not retained; the prior value was restored."))
            if repair_image and not product.image_1920:
                product.write(
                    {
                        "southern_source_url": prepared.get("before_source_url") or False,
                        "image_1920": False,
                    }
                )
                raise UserError(_("The listing image was not retained; prior values were restored."))
            stored_image = product.image_1920 or b""
            if isinstance(stored_image, str):
                stored_image = stored_image.encode("ascii", errors="ignore")
            item._refresh_readiness()
            applied.append(
                {
                    **{key: value for key, value in prepared.items() if key != "image_base64"},
                    "after_source_url_sha256": _sha256_text(product.southern_source_url),
                    "after_image_sha256": (
                        hashlib.sha256(stored_image).hexdigest()
                        if repair_image
                        else prepared.get("before_image_sha256")
                    ),
                }
            )
        return applied

    @api.model
    def rollback_source_links(self, records, reason):
        if not (reason or "").strip():
            raise UserError(_("Source-link rollback requires a reason."))
        for prepared in records or []:
            item = self.browse(int(prepared.get("item_id") or 0)).exists()
            if not item or item.normalized_sku != normalized_sparex_sku(prepared.get("sku")):
                continue
            product = item.matched_product_id.sudo()
            if product.id != int(prepared.get("product_id") or 0):
                continue
            self.env.cr.execute("SELECT id FROM product_template WHERE id = %s FOR UPDATE NOWAIT", [product.id])
            product.invalidate_recordset()
            expected_after = (prepared.get("after_source_url_sha256") or "").casefold()
            if expected_after and _sha256_text(product.southern_source_url or "") != expected_after:
                raise UserError(_("Source-link rollback stopped because the product URL changed after this run."))
            if prepared.get("repair_image") and not prepared.get("before_image_present"):
                expected_image = (prepared.get("after_image_sha256") or "").casefold()
                current_image = product.image_1920 or b""
                if isinstance(current_image, str):
                    current_image = current_image.encode("ascii", errors="ignore")
                if expected_image and hashlib.sha256(current_image).hexdigest() != expected_image:
                    raise UserError(_("Source-link rollback stopped because the product image changed after this run."))
            rollback_values = {"southern_source_url": prepared.get("before_source_url") or False}
            if prepared.get("repair_image") and not prepared.get("before_image_present"):
                rollback_values["image_1920"] = False
            product.write(rollback_values)
            item._refresh_readiness()
        return True
