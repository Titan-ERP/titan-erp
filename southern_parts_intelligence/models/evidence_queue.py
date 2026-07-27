import html
import json
import re
import time
import urllib.error
import urllib.request
from datetime import timedelta

from odoo import api, fields, models
from odoo.exceptions import UserError


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
            ("approved_for_apply", "Approved for Price Apply"),
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
    previous_observed_price = fields.Float(string="Previous Observed Price")
    price_delta = fields.Float(string="Price Delta", compute="_compute_price_delta", store=True)
    price_changed = fields.Boolean(string="Price Changed", compute="_compute_price_delta", store=True)
    pricing_formula = fields.Selection(
        [
            ("market_match", "Market Match"),
            ("manual_review", "Manual Review"),
        ],
        default="market_match",
        required=True,
        index=True,
    )
    recommended_sale_price = fields.Float(
        string="Recommended Sale Price",
        compute="_compute_recommended_sale_price",
        store=True,
    )
    approved_sale_price = fields.Float(string="Approved Sale Price")
    product_price_before_apply = fields.Float(string="Product Price Before Apply", readonly=True)
    product_price_after_apply = fields.Float(string="Product Price After Apply", readonly=True)
    currency_code = fields.Char(string="Currency", index=True)
    confidence = fields.Float(default=0.0)
    blocker_reason = fields.Char(index=True)
    last_refresh_error = fields.Char(readonly=True)
    notes = fields.Text()
    external_key = fields.Char(index=True)
    price_watch_enabled = fields.Boolean(default=True, index=True)
    retry_count = fields.Integer(default=0)
    last_checked_at = fields.Datetime()
    next_check_at = fields.Datetime(index=True)
    reviewed_at = fields.Datetime()
    applied_at = fields.Datetime()
    reviewed_by_id = fields.Many2one("res.users", readonly=True)
    applied_by_id = fields.Many2one("res.users", readonly=True)
    publication_ready = fields.Boolean(string="Publication Ready", compute="_compute_publication_readiness", store=True)
    publication_blocker_reason = fields.Char(string="Publication Blocker", compute="_compute_publication_readiness", store=True)

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

    @api.depends("observed_price", "previous_observed_price")
    def _compute_price_delta(self):
        for queue in self:
            queue.price_delta = queue.observed_price - queue.previous_observed_price
            queue.price_changed = bool(queue.previous_observed_price and abs(queue.price_delta) >= 0.01)

    @api.depends("evidence_type", "currency_code", "observed_price", "confidence", "pricing_formula")
    def _compute_recommended_sale_price(self):
        for queue in self:
            if (
                queue.evidence_type == "pricing"
                and queue.pricing_formula == "market_match"
                and (queue.currency_code or "").upper() == "USD"
                and queue.observed_price > 1.0
                and queue.confidence >= 0.8
            ):
                queue.recommended_sale_price = queue.observed_price
            else:
                queue.recommended_sale_price = 0.0

    @api.depends(
        "product_tmpl_id",
        "product_tmpl_id.active",
        "product_tmpl_id.sale_ok",
        "product_tmpl_id.list_price",
        "product_tmpl_id.public_categ_ids",
        "product_tmpl_id.image_1920",
        "product_tmpl_id.description_sale",
    )
    def _compute_publication_readiness(self):
        for queue in self:
            product = queue.product_tmpl_id
            blockers = []
            if not product:
                blockers.append("No linked product")
            else:
                if not product.active:
                    blockers.append("Product is archived")
                if not product.sale_ok:
                    blockers.append("Sales disabled")
                if product.list_price <= 1.0:
                    blockers.append("Sale price is not customer-ready")
                if "public_categ_ids" in product._fields and not product.public_categ_ids:
                    blockers.append("Missing ecommerce category")
                if "image_1920" in product._fields and not product.image_1920:
                    blockers.append("Missing product image")
                customer_copy = (product.description_sale or "").strip()
                if not customer_copy:
                    blockers.append("Missing sales description")
                internal_markers = (
                    "detail enrichment pending",
                    "pricing requires separate review",
                    "public blumaq page harvested",
                    "sparex source:",
                )
                if any(marker in customer_copy.lower() for marker in internal_markers):
                    blockers.append("Sales description contains internal sourcing text")
            queue.publication_ready = not blockers
            queue.publication_blocker_reason = "; ".join(blockers)

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
                product = self._find_product_by_default_code(vals["default_code"], Product)
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

    def action_link_product_by_sku(self):
        Product = self.env["product.template"].sudo()
        for queue in self:
            if queue.product_tmpl_id or not queue.default_code:
                continue
            product = queue._find_product_by_default_code(queue.default_code, Product)
            if product:
                queue.product_tmpl_id = product.id
        return True

    def action_approve_usd_price(self):
        for queue in self:
            queue._check_price_apply_ready(allow_approved_price_missing=True)
            queue.write(
                {
                    "status": "approved_for_apply",
                    "approved_sale_price": queue.recommended_sale_price or queue.observed_price,
                    "reviewed_at": fields.Datetime.now(),
                    "reviewed_by_id": self.env.user.id,
                    "blocker_reason": False,
                }
            )
        return True

    def action_apply_approved_price(self):
        for queue in self:
            queue._check_price_apply_ready()
            product = queue.product_tmpl_id.sudo()
            before_price = product.list_price
            product.write({"list_price": queue.approved_sale_price})
            queue.write(
                {
                    "status": "applied",
                    "product_price_before_apply": before_price,
                    "product_price_after_apply": queue.approved_sale_price,
                    "applied_at": fields.Datetime.now(),
                    "applied_by_id": self.env.user.id,
                    "blocker_reason": False,
                }
            )
        return True

    def _check_price_apply_ready(self, allow_approved_price_missing=False):
        self.ensure_one()
        if self.evidence_type != "pricing":
            raise UserError("Only pricing evidence can apply a sale price.")
        if not self.product_tmpl_id:
            raise UserError("A linked product is required before applying a price.")
        if (self.currency_code or "").upper() != "USD":
            raise UserError("Only exact USD pricing can be approved for direct price apply.")
        if self.observed_price <= 1.0:
            raise UserError("Observed price must be greater than $1.00 before applying.")
        if self.confidence < 0.8:
            raise UserError("Pricing confidence must be at least 0.80 before applying.")
        if not allow_approved_price_missing and self.approved_sale_price <= 1.0:
            raise UserError("Approved sale price must be greater than $1.00 before applying.")
        if not allow_approved_price_missing and self.status != "approved_for_apply":
            raise UserError("Price evidence must be approved before it can be applied.")

    def action_refresh_price_now(self):
        self.filtered(lambda row: row.evidence_type == "pricing").sudo()._refresh_price_observations()
        return True

    @api.model
    def _cron_refresh_price_evidence(self):
        now = fields.Datetime.now()
        domain = [
            ("active", "=", True),
            ("price_watch_enabled", "=", True),
            ("evidence_type", "=", "pricing"),
            ("status", "not in", ["blocked", "rejected"]),
            "|",
            ("source_url", "!=", False),
            ("source_search_url", "!=", False),
            "|",
            ("next_check_at", "=", False),
            ("next_check_at", "<=", now),
        ]
        batch = self.sudo().search(domain, order="priority desc, next_check_at, id", limit=25)
        batch._refresh_price_observations()

    def _refresh_price_observations(self):
        self.action_link_product_by_sku()
        for queue in self:
            if queue.evidence_type != "pricing":
                continue
            try:
                observation = queue._fetch_price_observation()
                if not observation:
                    queue.write(
                        {
                            "status": "alternate_source_needed",
                            "blocker_reason": "No exact current price found at saved source URL.",
                            "last_refresh_error": False,
                            "last_checked_at": fields.Datetime.now(),
                            "next_check_at": fields.Datetime.now() + timedelta(days=3),
                            "retry_count": queue.retry_count + 1,
                        }
                    )
                    continue
                vals = queue._observation_write_values(observation)
                queue.write(vals)
            except urllib.error.HTTPError as error:
                queue._write_refresh_error(f"HTTP {error.code}: {error.reason}", rate_limited=error.code == 429)
            except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
                queue._write_refresh_error(str(error))
            time.sleep(0.35)

    def _write_refresh_error(self, message, rate_limited=False):
        self.ensure_one()
        delay = timedelta(hours=6 if rate_limited else 24)
        next_check_at = fields.Datetime.now() + delay
        self.write(
            {
                "status": "rate_limited" if rate_limited else "alternate_source_needed",
                "blocker_reason": message[:255],
                "last_refresh_error": message[:255],
                "last_checked_at": fields.Datetime.now(),
                "next_check_at": next_check_at,
                "retry_count": self.retry_count + 1,
            }
        )
        if rate_limited and self.source_name:
            same_source = self.search(
                [
                    ("id", "!=", self.id),
                    ("active", "=", True),
                    ("price_watch_enabled", "=", True),
                    ("evidence_type", "=", "pricing"),
                    ("source_name", "=", self.source_name),
                    ("status", "not in", ["blocked", "rejected"]),
                    "|",
                    ("next_check_at", "=", False),
                    ("next_check_at", "<", next_check_at),
                ],
                limit=500,
            )
            same_source.write(
                {
                    "status": "rate_limited",
                    "blocker_reason": "Source rate-limited; supplier lane paused before retry.",
                    "last_refresh_error": message[:255],
                    "next_check_at": next_check_at,
                }
            )

    def _observation_write_values(self, observation):
        self.ensure_one()
        observed_price = observation["observed_price"]
        previous_price = self.observed_price or 0.0
        status = "ready_for_products_agent_review"
        if observation.get("currency_code") and observation["currency_code"] != "USD":
            status = "currency_review"
        blocker_reason = False
        priority = self.priority
        if previous_price and abs(observed_price - previous_price) >= 0.01:
            blocker_reason = "Observed retail price changed; review before applying."
            priority = "2"
        return {
            "status": status,
            "priority": priority,
            "previous_observed_price": previous_price,
            "observed_price": observed_price,
            "approved_sale_price": False,
            "currency_code": observation.get("currency_code"),
            "source_title": observation.get("source_title") or self.source_title,
            "source_url": observation.get("source_url") or self.source_url,
            "source_search_url": observation.get("source_search_url") or self.source_search_url,
            "confidence": observation.get("confidence", self.confidence),
            "blocker_reason": blocker_reason,
            "last_refresh_error": False,
            "last_checked_at": fields.Datetime.now(),
            "next_check_at": fields.Datetime.now() + timedelta(hours=12),
        }

    def _fetch_price_observation(self):
        self.ensure_one()
        sku = self._normalized_sparex_sku(self.default_code)
        source_name = (self.source_name or "").lower()
        fetch_url = self.source_search_url or self.source_url
        source = self._fetch_url(fetch_url)
        if "farming" in source_name:
            return self._parse_farming_parts(source, sku, fetch_url)
        if "lowe" in source_name or "young" in source_name:
            return self._parse_lowe_young(source, sku, fetch_url)
        return self._parse_generic_price(source, fetch_url)

    @api.model
    def _fetch_url(self, url):
        if not url:
            raise ValueError("Missing source URL.")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Southern Equipment Odoo retail evidence monitor",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            data = response.read()
            charset = response.headers.get_content_charset() or "utf-8"
        return data.decode(charset, errors="replace")

    @api.model
    def _normalized_sparex_sku(self, value):
        value = re.sub(r"\s+", "", (value or "").strip().upper())
        match = re.search(r"S\.?(\d+)", value)
        if not match:
            return value
        digits = str(int(match.group(1))) if match.group(1).isdigit() else match.group(1)
        return f"S.{digits}"

    @api.model
    def _sparex_sku_variants(self, value):
        raw = re.sub(r"\s+", "", (value or "").strip().upper())
        variants = []
        if raw:
            variants.append(raw)
        match = re.search(r"S\.?(\d+)", raw)
        if match:
            digits_raw = match.group(1)
            digits = str(int(digits_raw)) if digits_raw.isdigit() else digits_raw
            variants.extend(
                [
                    f"S.{digits_raw}",
                    f"S{digits_raw}",
                    f"S.{digits}",
                    f"S{digits}",
                ]
            )
        return list(dict.fromkeys(variants))

    @api.model
    def _find_product_by_default_code(self, default_code, Product=None):
        Product = Product or self.env["product.template"].sudo()
        for candidate in self._sparex_sku_variants(default_code):
            product = Product.search([("default_code", "=ilike", candidate)], limit=1)
            if product:
                return product
        return Product.browse()

    @api.model
    def _clean_text(self, value):
        text = html.unescape(value or "")
        text = re.sub(r"<[^>]+>", " ", text)
        return re.sub(r"\s+", " ", text).strip()

    def _parse_farming_parts(self, source, sku, fetch_url):
        meta_match = re.search(r"\bvar\s+meta\s*=\s*(\{.*?\});", source, flags=re.S)
        if not meta_match:
            return False
        payload = json.loads(meta_match.group(1))
        for product in payload.get("products", []):
            product_title = product.get("title") or ""
            product_url = "https://farmingparts.com/products/" + str(product.get("handle") or "").strip()
            for variant in product.get("variants", []):
                variant_sku = self._normalized_sparex_sku(variant.get("sku") or "")
                if variant_sku != sku:
                    continue
                cents = variant.get("price")
                if cents in (None, ""):
                    continue
                return {
                    "observed_price": round(float(cents) / 100.0, 2),
                    "currency_code": "GBP",
                    "source_title": self._clean_text(product_title or variant.get("name") or ""),
                    "source_url": product_url,
                    "source_search_url": fetch_url,
                    "confidence": 0.92,
                }
        return False

    def _parse_lowe_young(self, source, sku, fetch_url):
        sku_digits = re.sub(r"\D+", "", sku or "")
        patterns = [
            r"SPAREX-Part-S%s.*?<strong>\$(?P<price>[0-9,]+\.\d{2})</strong>" % re.escape(sku_digits),
            r"S\.?%s.*?\$(?P<price>[0-9,]+\.\d{2})" % re.escape(sku_digits),
        ]
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.S | re.I)
            if match:
                return {
                    "observed_price": float(match.group("price").replace(",", "")),
                    "currency_code": "USD",
                    "source_title": self.source_title,
                    "source_url": self.source_url or fetch_url,
                    "source_search_url": self.source_search_url,
                    "confidence": 0.9,
                }
        return False

    def _parse_generic_price(self, source, fetch_url):
        patterns = [
            r'"price"\s*:\s*"?(?P<price>[0-9,]+\.\d{2})"?',
            r'itemprop=["\']price["\'][^>]*content=["\'](?P<price>[0-9,]+\.\d{2})["\']',
            r'\$\s*(?P<price>[0-9,]+\.\d{2})',
        ]
        for pattern in patterns:
            match = re.search(pattern, source, flags=re.S | re.I)
            if match:
                return {
                    "observed_price": float(match.group("price").replace(",", "")),
                    "currency_code": self.currency_code or "USD",
                    "source_title": self.source_title,
                    "source_url": self.source_url or fetch_url,
                    "source_search_url": self.source_search_url,
                    "confidence": min(self.confidence or 0.75, 0.75),
                }
        return False

    _external_key_unique = models.Constraint(
        "unique(external_key)",
        "Evidence queue item already exists for this SKU, type, source, and URL.",
    )
