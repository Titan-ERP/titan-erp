import hashlib
import json
import re
import base64
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from urllib.parse import urlsplit

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .catalog_agents import (
    customer_description_ready,
    exact_sparex_url,
    normalized_sparex_sku,
    sparex_publication_blockers,
)

SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_CATALOG_UPSERT_BATCH = 2_000
MAX_PROMOTION_BATCH = 200
PROMOTION_CONFIRMATION = "vendor-catalog-product-promotion"
QUOTE_PUBLICATION_CONFIRMATION = "sparex-quote-only-publication"
MEDIA_CONFIRMATION = "sparex-media-batch-write"
OPERATIONAL_CONFIRMATION = "sparex-operational-batch-write"
GROSS_MARGIN_DENOMINATOR = Decimal("0.65")
PRICE_QUANTUM = Decimal("0.01")


def _canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_sku(value):
    return re.sub(r"\s+", "", (value or "").strip()).upper()[:128]


def _https_url(value):
    parsed = urlsplit((value or "").strip())
    return parsed.scheme.casefold() == "https" and bool(parsed.hostname)


def _decimal_value(value, field_name):
    try:
        decimal_value = Decimal(str(value or "0"))
    except (InvalidOperation, ValueError) as error:
        raise UserError(_("%s must be a decimal value.") % field_name) from error
    if not decimal_value.is_finite() or decimal_value < 0:
        raise UserError(_("%s cannot be negative or non-finite.") % field_name)
    return decimal_value


def _cost_plus_price(cost):
    return (cost / GROSS_MARGIN_DENOMINATOR).quantize(PRICE_QUANTUM, rounding=ROUND_CEILING)


class SouthernVendorCatalogSource(models.Model):
    _name = "southern.vendor.catalog.source"
    _description = "Vendor Catalog Source"
    _order = "sequence, name, id"

    name = fields.Char(required=True, index=True)
    code = fields.Char(required=True, index=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    partner_id = fields.Many2one(
        "res.partner",
        string="Vendor",
        domain="[('supplier_rank', '>', 0)]",
        ondelete="restrict",
        index=True,
    )
    source_type = fields.Selection(
        [
            ("web_listing", "Web Listing Pages"),
            ("sitemap", "Sitemap"),
            ("csv", "CSV"),
            ("xlsx", "Excel"),
            ("pdf", "PDF / Price Book"),
            ("email", "Email Attachment"),
            ("invoice", "Invoice / Purchase History"),
            ("manual", "Manual"),
        ],
        default="web_listing",
        required=True,
        index=True,
    )
    base_url = fields.Char()
    internal_reference_prefix = fields.Char(
        help="Optional prefix used when promoting a vendor SKU into an Odoo Internal Reference."
    )
    default_category_id = fields.Many2one("product.category", ondelete="restrict")
    automatic_promotion_enabled = fields.Boolean(
        default=False,
        help="Allows an approved external worker to promote evidence-complete staged items into unpublished products.",
    )
    promotion_batch_size = fields.Integer(default=100, required=True)
    schema_version = fields.Char(default="1.0", required=True)

    _code_company_unique = models.Constraint(
        "unique(code, company_id)", "Each vendor catalog source code must be unique within a company."
    )

    @api.constrains("code", "promotion_batch_size", "base_url")
    def _check_source_configuration(self):
        for source in self:
            normalized_code = re.sub(r"[^a-z0-9_-]+", "-", (source.code or "").strip().casefold()).strip("-")
            if not normalized_code or normalized_code != source.code:
                raise ValidationError(_("Catalog source codes must use lowercase letters, digits, underscores, or hyphens."))
            if not 1 <= source.promotion_batch_size <= MAX_PROMOTION_BATCH:
                raise ValidationError(_("Catalog promotion batches must contain between 1 and 200 products."))
            if source.base_url and not _https_url(source.base_url):
                raise ValidationError(_("Catalog source base URLs must use HTTPS."))


class SouthernVendorCatalogItem(models.Model):
    _name = "southern.vendor.catalog.item"
    _description = "Vendor Catalog Item"
    _rec_name = "title"
    _order = "promotion_requested desc, demand_count desc, last_seen_at desc, id desc"

    active = fields.Boolean(default=True, index=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    source_id = fields.Many2one("southern.vendor.catalog.source", required=True, ondelete="restrict", index=True)
    partner_id = fields.Many2one(related="source_id.partner_id", store=True, readonly=True, index=True)
    vendor_sku = fields.Char(required=True, readonly=True, index=True)
    normalized_sku = fields.Char(required=True, readonly=True, index=True)
    internal_reference = fields.Char(required=True, readonly=True, index=True)
    title = fields.Char(required=True, readonly=True, index=True)
    customer_description = fields.Text(readonly=True)
    category_path = fields.Char(readonly=True, index=True)
    source_url = fields.Char(required=True, readonly=True)
    image_url = fields.Char(readonly=True)
    vendor_cost = fields.Monetary(readonly=True, currency_field="currency_id")
    sales_price = fields.Monetary(readonly=True, currency_field="currency_id")
    currency_id = fields.Many2one("res.currency", required=True, readonly=True)
    availability = fields.Selection(
        [("unknown", "Unknown"), ("available", "Available"), ("backorder", "Backorder"), ("discontinued", "Discontinued")],
        default="unknown",
        required=True,
        readonly=True,
        index=True,
    )
    content_sha256 = fields.Char(required=True, readonly=True, index=True)
    source_artifact_uri = fields.Char(required=True, readonly=True)
    source_artifact_sha256 = fields.Char(required=True, readonly=True, index=True)
    schema_version = fields.Char(required=True, readonly=True)
    first_seen_at = fields.Datetime(required=True, readonly=True, index=True)
    last_seen_at = fields.Datetime(required=True, readonly=True, index=True)
    observation_count = fields.Integer(default=1, required=True, readonly=True)
    demand_count = fields.Integer(default=0, required=True, readonly=True, index=True)
    promotion_requested = fields.Boolean(default=False, readonly=True, index=True)
    last_requested_at = fields.Datetime(readonly=True, index=True)
    product_id = fields.Many2one("product.template", readonly=True, ondelete="set null", index=True)
    match_state = fields.Selection(
        [("missing", "Missing in Odoo"), ("matched", "Matched Product"), ("duplicate", "Duplicate Internal Reference")],
        default="missing",
        required=True,
        readonly=True,
        index=True,
    )
    promotion_state = fields.Selection(
        [("staged", "Staged"), ("ready", "Ready"), ("requested", "Requested"), ("promoted", "Promoted"), ("blocked", "Blocked")],
        default="staged",
        required=True,
        readonly=True,
        index=True,
    )
    blocker_code = fields.Selection(
        [
            ("missing_vendor", "Vendor Not Linked"),
            ("missing_title", "Missing Title"),
            ("missing_source_url", "Missing Source URL"),
            ("missing_image", "Missing Image"),
            ("missing_cost", "Missing Positive Cost"),
            ("missing_sales_price", "Missing Positive Sales Price"),
            ("price_below_cost", "Sales Price Does Not Exceed Cost"),
            ("missing_category", "Missing Odoo Category"),
            ("duplicate_product", "Duplicate Odoo Internal Reference"),
        ],
        readonly=True,
        index=True,
    )
    promoted_at = fields.Datetime(readonly=True, index=True)
    catalog_state = fields.Selection(
        [
            ("discovered", "Discovered"),
            ("blocked", "Blocked"),
            ("ready_for_promotion", "Ready for Promotion"),
            ("operational", "Operational"),
            ("unavailable", "Unavailable"),
            ("archived", "Archived"),
        ],
        default="discovered",
        required=True,
        readonly=True,
        index=True,
    )
    website_state = fields.Selection(
        [
            ("not_ready", "Not Ready"),
            ("ready_for_validation", "Ready for Validation"),
            ("validated", "Validated"),
            ("published", "Published"),
            ("publication_error", "Publication Error"),
        ],
        default="not_ready",
        required=True,
        readonly=True,
        index=True,
    )
    readiness_blockers_json = fields.Text(default="[]", required=True, readonly=True)
    page_sha256 = fields.Char(readonly=True, index=True)
    card_sha256 = fields.Char(readonly=True, index=True)
    image_source_sha256 = fields.Char(readonly=True, index=True)
    image_artifact_uri = fields.Char(readonly=True)
    image_artifact_sha256 = fields.Char(readonly=True, index=True)
    image_write_verified = fields.Boolean(default=False, readonly=True, index=True)
    validated_image_1920 = fields.Binary(readonly=True, attachment=True)
    pricing_basis = fields.Selection(
        [("none", "Not Set"), ("cost_plus_35_margin", "35% Gross Margin Cost Plus")],
        default="none",
        required=True,
        readonly=True,
        index=True,
    )
    pricing_margin_percent = fields.Float(default=0.0, readonly=True)
    pricing_calculated_at = fields.Datetime(readonly=True)
    dealer_currency_code = fields.Char(default="USD", required=True, readonly=True, index=True)
    last_seen_sweep_key = fields.Char(readonly=True, index=True)
    name_evidence_present = fields.Boolean(default=True, required=True, readonly=True)
    dealer_cost_evidence_uri = fields.Char(readonly=True)
    dealer_cost_evidence_sha256 = fields.Char(readonly=True, index=True)
    dealer_cost_observed_at = fields.Datetime(readonly=True, index=True)

    _source_sku_unique = models.Constraint(
        "unique(source_id, normalized_sku)", "Each vendor SKU can appear only once in a catalog source."
    )

    @api.constrains("content_sha256", "source_artifact_sha256", "vendor_cost", "sales_price")
    def _check_catalog_item(self):
        for item in self:
            if not SHA256_PATTERN.fullmatch((item.content_sha256 or "").casefold()) or not SHA256_PATTERN.fullmatch(
                (item.source_artifact_sha256 or "").casefold()
            ):
                raise ValidationError(_("Vendor catalog evidence hashes must be SHA-256 hexadecimal values."))
            if item.vendor_cost < 0 or item.sales_price < 0:
                raise ValidationError(_("Vendor catalog prices cannot be negative."))

    @api.model
    def _source_for_code(self, source_code):
        sources = self.env["southern.vendor.catalog.source"].sudo().search(
            [("company_id", "=", self.env.company.id), ("code", "=", (source_code or "").strip().casefold()), ("active", "=", True)],
            limit=2,
        )
        if len(sources) != 1:
            raise UserError(_("Exactly one active vendor catalog source must match the supplied source code."))
        if not sources.partner_id:
            partners = self.env["res.partner"].sudo().search(
                [("name", "=ilike", sources.name), ("supplier_rank", ">", 0)], limit=2
            )
            if len(partners) == 1:
                sources.write({"partner_id": partners.id})
        return sources

    @api.model
    def _match_product(self, internal_reference):
        products = self.env["product.template"].with_context(active_test=False).sudo().search(
            [("default_code", "=ilike", internal_reference)], limit=3
        )
        if len(products) > 1:
            return "duplicate", self.env["product.template"]
        if products:
            return "matched", products[:1]
        return "missing", self.env["product.template"]

    @api.model
    def _match_products(self, internal_references):
        """Match a bounded catalog batch with one product-table scan."""
        references = [value for value in dict.fromkeys(internal_references or []) if value]
        products_by_reference = {value.casefold(): [] for value in references}
        if references:
            self.env.cr.execute(
                """
                SELECT id
                  FROM product_template
                 WHERE LOWER(COALESCE(default_code, '')) = ANY(%s)
                """,
                [[value.casefold() for value in references]],
            )
            products = self.env["product.template"].with_context(active_test=False).browse(
                [row[0] for row in self.env.cr.fetchall()]
            )
            for product in products:
                key = (product.default_code or "").casefold()
                if key in products_by_reference:
                    products_by_reference[key].append(product.id)
        result = {}
        for reference in references:
            product_ids = products_by_reference[reference.casefold()]
            if len(product_ids) > 1:
                result[reference] = ("duplicate", self.env["product.template"])
            elif product_ids:
                result[reference] = (
                    "matched",
                    self.env["product.template"].with_context(active_test=False).browse(product_ids[:1]),
                )
            else:
                result[reference] = ("missing", self.env["product.template"])
        return result

    @api.model
    def _readiness(self, source, values, match_state):
        blockers = []
        if match_state == "duplicate":
            blockers.append("identity_conflict")
        if not source.partner_id:
            blockers.append("supplier_conflict")
        if not values.get("title") or not values.get("name_evidence_present", True):
            blockers.append("missing_name")
        source_url_valid = _https_url(values.get("source_url"))
        if source.code == "sparex":
            source_url_valid = source_url_valid and exact_sparex_url(
                values.get("source_url"), values.get("normalized_sku")
            )
        if not source_url_valid:
            blockers.append("missing_exact_url")
        if _decimal_value(values.get("vendor_cost"), "Dealer cost") <= 0:
            blockers.append("missing_cost")
        if values.get("dealer_currency_code") != "USD":
            blockers.append("invalid_currency")
        if not values.get("image_artifact_sha256"):
            blockers.append("missing_image_artifact")
        if not values.get("image_write_verified"):
            blockers.append("image_write_unverified")
        if not source.default_category_id:
            blockers.append("category_unmapped")
        sales_price = _decimal_value(values.get("sales_price"), "Sales price")
        vendor_cost = _decimal_value(values.get("vendor_cost"), "Dealer cost")
        if vendor_cost > 0 and sales_price <= vendor_cost:
            blockers.append("pricing_error")
        legacy_blocker = {
            "identity_conflict": "duplicate_product",
            "supplier_conflict": "missing_vendor",
            "missing_name": "missing_title",
            "missing_exact_url": "missing_source_url",
            "missing_cost": "missing_cost",
            "missing_image_artifact": "missing_image",
            "image_write_unverified": "missing_image",
            "category_unmapped": "missing_category",
            "pricing_error": "price_below_cost",
        }
        if match_state == "matched" and not blockers:
            return "ready", False, "ready_for_promotion", blockers
        if blockers:
            state = "blocked" if any(value in {"identity_conflict", "supplier_conflict", "invalid_currency", "pricing_error"} for value in blockers) else "staged"
            return state, legacy_blocker.get(blockers[0]), "blocked", blockers
        return "ready", False, "ready_for_promotion", blockers

    def _recompute_readiness(self):
        for item in self:
            values = {
                "title": item.title,
                "name_evidence_present": item.name_evidence_present,
                "normalized_sku": item.normalized_sku,
                "source_url": item.source_url,
                "vendor_cost": item.vendor_cost,
                "sales_price": item.sales_price,
                "dealer_currency_code": item.dealer_currency_code,
                "image_artifact_sha256": item.image_artifact_sha256,
                "image_write_verified": item.image_write_verified,
            }
            promotion_state, blocker_code, catalog_state, blockers = item._readiness(
                item.source_id, values, item.match_state
            )
            if item.promotion_requested and promotion_state == "ready":
                promotion_state = "requested"
            item.write(
                {
                    "promotion_state": promotion_state,
                    "blocker_code": blocker_code,
                    "catalog_state": "unavailable" if item.availability == "discontinued" else catalog_state,
                    "readiness_blockers_json": json.dumps(blockers, separators=(",", ":")),
                }
            )
        return True

    @api.model
    def upsert_catalog_items(
        self,
        source_code,
        records,
        artifact_uri,
        artifact_sha256,
        schema_version="1.0",
    ):
        records = list(records or [])
        artifact_uri = (artifact_uri or "").strip()
        artifact_sha256 = (artifact_sha256 or "").strip().casefold()
        if not records or len(records) > MAX_CATALOG_UPSERT_BATCH:
            raise UserError(_("Catalog upserts must contain between 1 and 2,000 items."))
        if not artifact_uri.startswith("s3://") or not SHA256_PATTERN.fullmatch(artifact_sha256):
            raise UserError(_("Catalog upserts require an archived S3 artifact and SHA-256 checksum."))
        source = self._source_for_code(source_code)
        from .sparex_manifest import acquire_sparex_catalog_lock

        acquire_sparex_catalog_lock(self.env)
        now = fields.Datetime.now()
        normalized_records = {}
        for record in records:
            record = dict(record or {})
            vendor_sku = (record.get("vendor_sku") or record.get("sku") or "").strip()[:128]
            normalized = _normalized_sku(record.get("normalized_sku") or vendor_sku)
            observed_title = " ".join((record.get("title") or record.get("listing_title") or "").split()).strip()[:255]
            title = observed_title or vendor_sku
            source_url = (record.get("source_url") or "").strip()
            image_url = (record.get("image_url") or "").strip()
            if not vendor_sku or not normalized or normalized in normalized_records:
                raise UserError(_("Each catalog batch item must contain one unique vendor SKU."))
            if not _https_url(source_url) or (image_url and not _https_url(image_url)):
                raise UserError(_("Catalog items require an HTTPS source URL and optional HTTPS image URL."))
            prefix = (source.internal_reference_prefix or "").strip()
            internal_reference = f"{prefix}{normalized}"[:128]
            vendor_cost = _decimal_value(record.get("vendor_cost"), "Dealer cost")
            supplied_sales_price = _decimal_value(record.get("sales_price"), "Sales price")
            sales_price = supplied_sales_price or (_cost_plus_price(vendor_cost) if vendor_cost > 0 else Decimal("0"))
            image_artifact_sha256 = str(record.get("image_artifact_sha256") or "").strip().casefold()
            if image_artifact_sha256 and not SHA256_PATTERN.fullmatch(image_artifact_sha256):
                raise UserError(_("Catalog image artifacts require a SHA-256 checksum."))
            currency_code = str(record.get("currency") or record.get("currency_code") or "USD").strip().upper()
            canonical_hash_values = {
                "vendor_sku": vendor_sku,
                "normalized_sku": normalized,
                "internal_reference": internal_reference,
                "title": title,
                "name_evidence_present": bool(observed_title),
                "customer_description": (record.get("customer_description") or "").strip(),
                "category_path": (record.get("category_path") or "").strip()[:255],
                "source_url": source_url,
                "image_url": image_url,
                "vendor_cost": format(vendor_cost, "f"),
                "sales_price": format(sales_price, "f"),
                "dealer_currency_code": currency_code,
                "availability": record.get("availability") if record.get("availability") in {"unknown", "available", "backorder", "discontinued"} else "unknown",
                "page_sha256": str(record.get("page_sha256") or "").casefold(),
                "card_sha256": str(record.get("card_sha256") or "").casefold(),
                "image_source_sha256": str(
                    record.get("image_source_sha256")
                    or (hashlib.sha256(image_url.encode("utf-8")).hexdigest() if image_url else "")
                ).casefold(),
                "image_artifact_uri": str(record.get("image_artifact_uri") or "").strip(),
                "image_artifact_sha256": image_artifact_sha256,
                "image_write_verified": bool(record.get("image_write_verified")),
            }
            canonical = {
                **canonical_hash_values,
                "vendor_cost": float(vendor_cost),
                "sales_price": float(sales_price),
                "pricing_basis": "cost_plus_35_margin" if vendor_cost > 0 and not supplied_sales_price else "none",
                "pricing_margin_percent": 35.0 if vendor_cost > 0 and not supplied_sales_price else 0.0,
                "pricing_calculated_at": fields.Datetime.now() if vendor_cost > 0 and not supplied_sales_price else False,
            }
            canonical["content_sha256"] = _canonical_sha256(canonical_hash_values)
            normalized_records[normalized] = canonical
        product_matches = self._match_products(
            [values["internal_reference"] for values in normalized_records.values()]
        )
        existing = self.sudo().search(
            [("source_id", "=", source.id), ("normalized_sku", "in", list(normalized_records))]
        )
        existing_by_sku = {item.normalized_sku: item for item in existing}
        created = updated = unchanged = ready = 0
        create_values = []
        for normalized, values in normalized_records.items():
            item = existing_by_sku.get(normalized)
            if item and item.image_source_sha256 == values.get("image_source_sha256") and item.image_write_verified:
                values.update(
                    {
                        "image_artifact_uri": item.image_artifact_uri,
                        "image_artifact_sha256": item.image_artifact_sha256,
                        "image_write_verified": True,
                    }
                )
            match_state, product = product_matches[values["internal_reference"]]
            promotion_state, blocker_code, catalog_state, readiness_blockers = self._readiness(source, values, match_state)
            if item and item.promotion_requested and promotion_state == "ready":
                promotion_state = "requested"
            write_values = {
                **values,
                "currency_id": source.company_id.currency_id.id,
                "source_artifact_uri": artifact_uri,
                "source_artifact_sha256": artifact_sha256,
                "schema_version": (schema_version or source.schema_version or "1.0")[:32],
                "last_seen_at": now,
                "match_state": match_state,
                "product_id": product.id if product else False,
                "promotion_state": promotion_state,
                "blocker_code": blocker_code,
                "catalog_state": "unavailable" if values["availability"] == "discontinued" else catalog_state,
                "readiness_blockers_json": json.dumps(readiness_blockers, separators=(",", ":")),
                "active": values["availability"] != "discontinued",
                "last_seen_sweep_key": self.env.context.get("sparex_sweep_key") or (item.last_seen_sweep_key if item else False),
            }
            if item:
                write_values["observation_count"] = item.observation_count + 1
                if item.content_sha256 == values["content_sha256"] and item.source_artifact_sha256 == artifact_sha256:
                    item.write(
                        {
                            "last_seen_at": now,
                            "observation_count": item.observation_count + 1,
                            "source_artifact_uri": artifact_uri,
                            "source_artifact_sha256": artifact_sha256,
                        }
                    )
                    unchanged += 1
                else:
                    item.write(write_values)
                    updated += 1
            else:
                create_values.append(
                    {
                        **write_values,
                        "company_id": source.company_id.id,
                        "source_id": source.id,
                        "first_seen_at": now,
                    }
                )
                created += 1
            if promotion_state in {"ready", "requested"}:
                ready += 1
        if create_values:
            self.sudo().create(create_values)
        return {"created": created, "updated": updated, "unchanged": unchanged, "ready": ready, "observed": len(records)}

    def action_request_promotion(self):
        now = fields.Datetime.now()
        for item in self:
            if item.match_state == "matched":
                continue
            item.write(
                {
                    "promotion_requested": True,
                    "demand_count": item.demand_count + 1,
                    "last_requested_at": now,
                    "promotion_state": "requested" if not item.blocker_code else item.promotion_state,
                }
            )
        return True

    def _promotion_snapshot(self):
        self.ensure_one()
        snapshot = {
            "item_id": self.id,
            "source_id": self.source_id.id,
            "source_write_date": str(self.source_id.write_date or ""),
            "item_write_date": str(self.write_date or ""),
            "content_sha256": self.content_sha256,
            "internal_reference": self.internal_reference,
            "title": self.title,
            "customer_description": self.customer_description or self.title,
            "source_url": self.source_url,
            "image_url": self.image_url,
            "vendor_cost": self.vendor_cost,
            "sales_price": self.sales_price,
            "currency_id": self.currency_id.id,
            "category_id": self.source_id.default_category_id.id,
            "partner_id": self.source_id.partner_id.id,
        }
        snapshot["snapshot_sha256"] = _canonical_sha256(snapshot)
        return snapshot

    @api.model
    def prepare_promotion_plan(self, source_code=False, item_ids=None, limit=MAX_PROMOTION_BATCH):
        bounded = max(1, min(int(limit or MAX_PROMOTION_BATCH), MAX_PROMOTION_BATCH))
        domain = [("company_id", "=", self.env.company.id), ("match_state", "=", "missing"), ("blocker_code", "=", False), ("promotion_state", "in", ["ready", "requested"])]
        if source_code:
            domain.append(("source_id", "=", self._source_for_code(source_code).id))
        if item_ids:
            domain.append(("id", "in", [int(item_id) for item_id in item_ids]))
        items = self.sudo().search(domain, order="promotion_requested desc, demand_count desc, last_seen_at, id", limit=bounded)
        return [item._promotion_snapshot() for item in items]

    @api.model
    def apply_promotion_plan(self, records, artifact_uri, artifact_sha256, confirmation, reason):
        records = list(records or [])
        if confirmation != PROMOTION_CONFIRMATION or not (reason or "").strip():
            raise UserError(_("Vendor catalog promotion requires explicit confirmation and a business reason."))
        if not records or len(records) > MAX_PROMOTION_BATCH:
            raise UserError(_("Vendor catalog promotion plans must contain between 1 and 200 products."))
        if not (artifact_uri or "").startswith("s3://") or not SHA256_PATTERN.fullmatch((artifact_sha256 or "").casefold()):
            raise UserError(_("Vendor catalog promotion requires an archived SHA-256 plan artifact."))
        from .sparex_manifest import acquire_sparex_catalog_lock

        acquire_sparex_catalog_lock(self.env)
        promoted = []
        for prepared in records:
            item = self.sudo().browse(int(prepared.get("item_id") or 0)).exists()
            if not item or item.company_id != self.env.company:
                raise UserError(_("The prepared vendor catalog item is unavailable."))
            self.env.cr.execute("SELECT id FROM southern_vendor_catalog_item WHERE id = %s FOR UPDATE NOWAIT", [item.id])
            item.invalidate_recordset()
            if not item.source_id.automatic_promotion_enabled:
                raise UserError(_("Automatic promotion is disabled for vendor source %s.") % item.source_id.name)
            current = item._promotion_snapshot()
            if item.match_state != "missing" or item.blocker_code or current != prepared:
                raise UserError(_("Vendor catalog evidence changed; prepare a fresh promotion plan."))
            expected_sha = _canonical_sha256({key: value for key, value in prepared.items() if key != "snapshot_sha256"})
            if expected_sha != (prepared.get("snapshot_sha256") or "").casefold():
                raise UserError(_("The vendor catalog promotion snapshot checksum is invalid."))
            match_state, product = self._match_product(item.internal_reference)
            created = not bool(product)
            if match_state == "duplicate":
                raise UserError(_("The Internal Reference became duplicated after the promotion plan was prepared."))
            if created:
                product = self.env["product.template"].sudo().create(
                    {
                        "name": item.title,
                        "default_code": item.internal_reference,
                        "active": True,
                        "sale_ok": True,
                        "purchase_ok": True,
                        "categ_id": item.source_id.default_category_id.id,
                        "list_price": item.sales_price,
                        "standard_price": item.vendor_cost,
                        "description_sale": item.customer_description or item.title,
                        "southern_source_name": item.source_id.name,
                        "southern_source_url": item.source_url,
                        "southern_enrichment_status": "partial",
                        "southern_price_basis": "cost_plus",
                        "southern_cost_plus_margin_percent": item.pricing_margin_percent,
                        "southern_price_basis_updated_at": item.pricing_calculated_at,
                        "southern_sparex_evidence_sha256": item.content_sha256,
                        "southern_sparex_image_sha256": item.image_artifact_sha256,
                        "image_1920": item.validated_image_1920,
                        "website_published": False,
                    }
                )
                self.env["product.supplierinfo"].sudo().create(
                    {
                        "partner_id": item.source_id.partner_id.id,
                        "product_tmpl_id": product.id,
                        "product_code": item.vendor_sku,
                        "price": item.vendor_cost,
                        "min_qty": 1.0,
                        "company_id": item.company_id.id,
                    }
                )
            item.write(
                {
                    "product_id": product.id,
                    "match_state": "matched",
                    "promotion_state": "promoted",
                    "catalog_state": "operational",
                    "blocker_code": False,
                    "readiness_blockers_json": "[]",
                    "promotion_requested": False,
                    "promoted_at": fields.Datetime.now(),
                }
            )
            promoted.append(
                {
                    "item_id": item.id,
                    "product_id": product.id,
                    "internal_reference": item.internal_reference,
                    "created": created,
                    "website_published": bool(product.website_published),
                    "artifact_uri": artifact_uri,
                    "artifact_sha256": artifact_sha256,
                }
            )
        return promoted

    @api.model
    def apply_media_batch(self, records, artifact_uri, artifact_sha256, confirmation):
        records = list(records or [])
        if confirmation != MEDIA_CONFIRMATION:
            raise UserError(_("Sparex media writes require explicit confirmation."))
        if not 1 <= len(records) <= 25:
            raise UserError(_("Sparex media batches must contain between 1 and 25 images."))
        if not str(artifact_uri or "").startswith("s3://") or not SHA256_PATTERN.fullmatch(
            str(artifact_sha256 or "").casefold()
        ):
            raise UserError(_("Sparex media batches require an archived SHA-256 artifact."))
        from .sparex_manifest import acquire_sparex_catalog_lock

        acquire_sparex_catalog_lock(self.env)
        results = []
        for prepared in records:
            item = self.sudo().browse(int(prepared.get("item_id") or 0)).exists()
            if not item:
                raise UserError(_("The prepared catalog item is unavailable."))
            expected_source = str(prepared.get("source_image_sha256") or "").casefold()
            expected_content = str(prepared.get("image_sha256") or "").casefold()
            if expected_source != (item.image_source_sha256 or ""):
                raise UserError(_("The Sparex source image evidence changed; prepare a fresh media batch."))
            if not SHA256_PATTERN.fullmatch(expected_content):
                raise UserError(_("Sparex image content requires a SHA-256 checksum."))
            prepared_artifact_sha = str(prepared.get("image_artifact_sha256") or expected_content).casefold()
            if prepared_artifact_sha != expected_content:
                raise UserError(_("Sparex image artifact and content checksums do not match."))
            try:
                content = base64.b64decode(prepared.get("image_base64") or "", validate=True)
            except (ValueError, TypeError) as error:
                raise UserError(_("Sparex image content is not valid base64.")) from error
            if not content or len(content) > 10 * 1024 * 1024 or hashlib.sha256(content).hexdigest() != expected_content:
                raise UserError(_("Sparex image content failed size or checksum validation."))
            signatures = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF87a", b"GIF89a", b"RIFF")
            if not content.startswith(signatures):
                raise UserError(_("Sparex media must be a verified PNG, JPEG, GIF, or WebP image."))
            product = item.product_id.sudo()
            if product and product.southern_sparex_image_override:
                blockers = json.loads(item.readiness_blockers_json or "[]")
                if "image_write_unverified" not in blockers:
                    blockers.append("image_write_unverified")
                item.write({"readiness_blockers_json": json.dumps(blockers, separators=(",", ":"))})
                results.append({"item_id": item.id, "product_id": product.id, "status": "manual_override"})
                continue
            item.write(
                {
                    "validated_image_1920": base64.b64encode(content),
                    "image_artifact_uri": str(prepared.get("image_artifact_uri") or artifact_uri),
                    "image_artifact_sha256": expected_content,
                }
            )
            item.invalidate_recordset(["validated_image_1920"])
            staged_content = base64.b64decode(item.validated_image_1920 or b"")
            verified = hashlib.sha256(staged_content).hexdigest() == expected_content
            if product and product.southern_sparex_image_sha256 != expected_content:
                product.write({"image_1920": base64.b64encode(content), "southern_sparex_image_sha256": expected_content})
                product.invalidate_recordset(["image_1920", "southern_sparex_image_sha256"])
            if product:
                stored = base64.b64decode(product.image_1920 or b"")
                verified = verified and hashlib.sha256(stored).hexdigest() == expected_content
            item.write(
                {
                    "image_write_verified": verified,
                }
            )
            item._recompute_readiness()
            results.append(
                {"item_id": item.id, "product_id": product.id if product else False, "status": "verified" if verified else "failed"}
            )
        return results

    @api.model
    def apply_dealer_cost_evidence_batch(self, records, artifact_uri, artifact_sha256, parser_version):
        records = list(records or [])
        if not 1 <= len(records) <= 50:
            raise UserError(_("Dealer-cost evidence batches must contain between 1 and 50 items."))
        if not str(artifact_uri or "").startswith("s3://") or not SHA256_PATTERN.fullmatch(
            str(artifact_sha256 or "").casefold()
        ):
            raise UserError(_("Dealer-cost evidence requires an immutable S3 artifact and SHA-256 checksum."))
        source = self._source_for_code("sparex")
        normalized_skus = [_normalized_sku(record.get("sku")) for record in records]
        items = self.sudo().search(
            [("source_id", "=", source.id), ("normalized_sku", "in", normalized_skus)]
        )
        by_sku = {item.normalized_sku: item for item in items}
        upserts = []
        media = []
        for record in records:
            normalized = _normalized_sku(record.get("sku"))
            item = by_sku.get(normalized)
            if not item or not exact_sparex_url(record.get("evidence_url"), normalized):
                raise UserError(_("Dealer-cost evidence does not match an exact staged Sparex SKU."))
            cost = _decimal_value(record.get("dealer_price"), "Dealer cost")
            if cost <= 0 or str(record.get("currency") or "").upper() != "USD":
                raise UserError(_("Dealer-cost evidence must contain one positive exact USD value."))
            page_sha = str(record.get("evidence_sha256") or "").casefold()
            if not SHA256_PATTERN.fullmatch(page_sha):
                raise UserError(_("Dealer-cost page evidence requires a SHA-256 checksum."))
            image_url = str(record.get("detail_image_url") or item.image_url or "").strip()
            upserts.append(
                {
                    "vendor_sku": item.vendor_sku,
                    "normalized_sku": item.normalized_sku,
                    "title": str(record.get("detail_title") or item.title),
                    "customer_description": item.customer_description or "",
                    "category_path": item.category_path or "",
                    "source_url": str(record.get("evidence_url") or item.source_url),
                    "image_url": image_url,
                    "vendor_cost": format(cost, "f"),
                    "sales_price": "0",
                    "currency_code": "USD",
                    "availability": item.availability,
                    "page_sha256": page_sha,
                    "card_sha256": item.card_sha256 or "",
                    "image_source_sha256": hashlib.sha256(image_url.encode()).hexdigest() if image_url else "",
                }
            )
            if record.get("detail_image_base64") and record.get("detail_image_sha256"):
                media.append(
                    {
                        "normalized_sku": normalized,
                        "source_image_sha256": hashlib.sha256(image_url.encode()).hexdigest(),
                        "image_sha256": str(record["detail_image_sha256"]).casefold(),
                        "image_artifact_sha256": str(record.get("detail_image_artifact_sha256") or "").casefold(),
                        "image_base64": record["detail_image_base64"],
                        "image_artifact_uri": str(record.get("detail_image_artifact_uri") or artifact_uri),
                    }
                )
        result = self.upsert_catalog_items(
            "sparex", upserts, artifact_uri, artifact_sha256, schema_version=parser_version
        )
        refreshed = self.sudo().search(
            [("source_id", "=", source.id), ("normalized_sku", "in", normalized_skus)]
        )
        refreshed_by_sku = {item.normalized_sku: item for item in refreshed}
        now = fields.Datetime.now()
        refreshed.write(
            {
                "dealer_cost_evidence_uri": artifact_uri,
                "dealer_cost_evidence_sha256": artifact_sha256,
                "dealer_cost_observed_at": now,
            }
        )
        if media:
            for prepared in media:
                prepared["item_id"] = refreshed_by_sku[prepared.pop("normalized_sku")].id
            self.apply_media_batch(media, artifact_uri, artifact_sha256, MEDIA_CONFIRMATION)
        return {**result, "item_ids": refreshed.ids}

    @api.model
    def apply_operational_batch(self, item_ids, confirmation, reason):
        item_ids = [int(item_id) for item_id in list(item_ids or [])]
        if confirmation != OPERATIONAL_CONFIRMATION or not str(reason or "").strip():
            raise UserError(_("Sparex operational updates require explicit confirmation and a reason."))
        if not 1 <= len(item_ids) <= 100:
            raise UserError(_("Sparex operational batches must contain between 1 and 100 products."))
        from .sparex_manifest import acquire_sparex_catalog_lock

        acquire_sparex_catalog_lock(self.env)
        items = self.sudo().browse(item_ids).exists()
        if len(items) != len(set(item_ids)):
            raise UserError(_("One or more Sparex staging items are unavailable."))
        SupplierInfo = self.env["product.supplierinfo"].sudo()
        results = []
        for item in items:
            item._recompute_readiness()
            if item.source_id.code != "sparex" or item.match_state != "matched" or not item.product_id:
                raise UserError(_("Sparex operational updates require one exactly matched product."))
            if item.readiness_blockers_json != "[]":
                raise UserError(_("Sparex item %s still has readiness blockers.") % item.vendor_sku)
            product = item.product_id.sudo()
            values = {
                "southern_source_name": item.source_id.name,
                "southern_source_url": item.source_url,
                "southern_sparex_evidence_sha256": item.content_sha256,
                "standard_price": item.vendor_cost,
                "purchase_ok": True,
                "sale_ok": True,
                "active": item.availability != "discontinued",
                "southern_enrichment_status": "complete",
                "southern_price_basis": "cost_plus",
                "southern_cost_plus_margin_percent": item.pricing_margin_percent,
                "southern_price_basis_updated_at": item.pricing_calculated_at,
            }
            if not product.southern_sparex_name_override:
                values["name"] = item.title
            if not product.southern_sparex_category_override:
                values["categ_id"] = item.source_id.default_category_id.id
            if not product.southern_sparex_description_override:
                values["description_sale"] = item.customer_description or item.title
            if not product.southern_sparex_price_override:
                values["list_price"] = item.sales_price
            changed = {}
            for name, value in values.items():
                current = product[name]
                current = current.id if hasattr(current, "id") else current
                if current != value:
                    changed[name] = value
            if changed:
                product.write(changed)
            suppliers = SupplierInfo.search(
                [
                    ("partner_id", "=", item.source_id.partner_id.id),
                    ("product_tmpl_id", "=", product.id),
                    ("product_code", "=", item.vendor_sku),
                    ("company_id", "in", [False, item.company_id.id]),
                ],
                limit=2,
            )
            if len(suppliers) > 1:
                raise UserError(_("Sparex item %s has duplicate supplier records.") % item.vendor_sku)
            supplier_values = {
                "partner_id": item.source_id.partner_id.id,
                "product_tmpl_id": product.id,
                "product_code": item.vendor_sku,
                "price": item.vendor_cost,
                "min_qty": 1.0,
                "company_id": item.company_id.id,
            }
            if suppliers:
                supplier_changes = {}
                for name, value in supplier_values.items():
                    current = suppliers[name]
                    current = current.id if hasattr(current, "id") else current
                    if current != value:
                        supplier_changes[name] = value
                if supplier_changes:
                    suppliers.write(supplier_changes)
            else:
                suppliers = SupplierInfo.create(supplier_values)
            item.write({"catalog_state": "operational", "promotion_state": "promoted", "promoted_at": fields.Datetime.now()})
            results.append(
                {
                    "item_id": item.id,
                    "product_id": product.id,
                    "product_fields_written": sorted(changed),
                    "supplierinfo_id": suppliers.id if suppliers else False,
                }
            )
        return results

    @api.model
    def _publication_fields(self):
        details = self.env["product.template"].fields_get(
            ["is_published", "website_published"], attributes=["readonly"]
        )
        fields_to_write = [
            name
            for name in ("is_published", "website_published")
            if name in details and not details[name].get("readonly")
        ]
        if not fields_to_write:
            raise UserError(_("No writable website publication field is available."))
        return fields_to_write

    @api.model
    def _quote_publication_source(self, company_id=False):
        allowed_company_ids = self.env.user.company_ids.ids
        domain = [
            ("code", "=", "sparex"),
            ("active", "=", True),
            ("company_id", "in", allowed_company_ids),
        ]
        if company_id:
            domain.append(("company_id", "=", int(company_id)))
        sources = self.env["southern.vendor.catalog.source"].sudo().search(domain, limit=2)
        if len(sources) != 1:
            raise UserError(_("Exactly one authorized Sparex catalog source must match the selected company."))
        return sources

    def _quote_publication_snapshot(self):
        self.ensure_one()
        product = self.product_id.sudo()
        normalized = normalized_sparex_sku(product.default_code)
        descriptions = {
            field_name: product[field_name] or ""
            for field_name in ("description_ecommerce", "website_description", "description_sale")
            if field_name in product._fields
        }
        publication_fields = self._publication_fields()
        customer_copy = (
            f"{product.name}. Contact Southern Equipment for current pricing, fitment, and availability."
        )
        descriptions_after = dict(descriptions)
        if not customer_description_ready(product):
            descriptions_after = {field_name: customer_copy for field_name in descriptions}
        source_url_after = product.southern_source_url or self.source_url
        if not exact_sparex_url(source_url_after, normalized):
            source_url_after = self.source_url
        snapshot = {
            "item_id": self.id,
            "company_id": self.company_id.id,
            "source_id": self.source_id.id,
            "product_id": product.id,
            "sku": normalized,
            "item_write_date": str(self.write_date or ""),
            "product_write_date": str(product.write_date or ""),
            "content_sha256": self.content_sha256,
            "source_artifact_uri": self.source_artifact_uri,
            "source_artifact_sha256": self.source_artifact_sha256,
            "source_name_before": product.southern_source_name or "",
            "source_url_before": product.southern_source_url or "",
            "source_url_after": source_url_after,
            "descriptions_before": descriptions,
            "descriptions_after": descriptions_after,
            "list_price_before": product.list_price,
            "quote_only_before": bool(product.southern_quote_only),
            "publication_fields_before": {name: bool(product[name]) for name in publication_fields},
        }
        snapshot["snapshot_sha256"] = _canonical_sha256(snapshot)
        return snapshot

    def _quote_publication_blockers(self):
        self.ensure_one()
        product = self.product_id.sudo()
        normalized = normalized_sparex_sku(product.default_code) if product else ""
        blockers = []
        checks = (
            (self.active, "staged_item_inactive"),
            (self.source_id.code == "sparex", "not_sparex_source"),
            (self.match_state == "matched" and bool(product), "missing_matched_product"),
            (bool(normalized), "invalid_sparex_sku"),
            (exact_sparex_url(self.source_url, normalized), "missing_exact_sparex_url"),
            (self.source_artifact_uri.startswith("s3://"), "missing_s3_evidence"),
            (
                bool(SHA256_PATTERN.fullmatch((self.source_artifact_sha256 or "").casefold())),
                "invalid_evidence_sha256",
            ),
            (bool(product and product.active), "product_archived"),
            (bool(product and product.sale_ok), "product_not_saleable"),
            (bool(product and not product.website_published), "already_published"),
            (bool(product and float(product.list_price or 0.0) <= 1.49), "non_placeholder_sales_price"),
            (bool(product and product.image_1920), "missing_image"),
            (bool(product and product.public_categ_ids), "missing_website_category"),
            (bool(product and product.name), "missing_product_name"),
        )
        for passed, blocker in checks:
            if not passed:
                blockers.append(blocker)
        return blockers

    def _quote_publication_eligible(self):
        self.ensure_one()
        return not self._quote_publication_blockers()

    @api.model
    def quote_publication_diagnostics(self, limit=5_000, company_id=False):
        bounded = max(1, min(int(limit or 5_000), 5_000))
        source = self._quote_publication_source(company_id)
        items = self.sudo().search(
            [
                ("company_id", "=", source.company_id.id),
                ("source_id", "=", source.id),
                ("match_state", "=", "matched"),
                ("product_id", "!=", False),
                ("active", "=", True),
            ],
            order="last_seen_at desc, id",
            limit=bounded,
        )
        counts = {}
        eligible = 0
        for item in items:
            blockers = item._quote_publication_blockers()
            if not blockers:
                eligible += 1
            for blocker in blockers:
                counts[blocker] = counts.get(blocker, 0) + 1
        return {"scanned": len(items), "eligible": eligible, "blockers": counts}

    @api.model
    def prepare_quote_publication_plan(self, limit=MAX_PROMOTION_BATCH, company_id=False):
        bounded = max(1, min(int(limit or MAX_PROMOTION_BATCH), MAX_PROMOTION_BATCH))
        source = self._quote_publication_source(company_id)
        items = self.sudo().search(
            [
                ("company_id", "=", source.company_id.id),
                ("source_id", "=", source.id),
                ("match_state", "=", "matched"),
                ("product_id", "!=", False),
                ("active", "=", True),
            ],
            order="last_seen_at desc, id",
            limit=bounded * 8,
        )
        records = []
        seen_products = set()
        for item in items:
            if item.product_id.id in seen_products or not item._quote_publication_eligible():
                continue
            records.append(item._quote_publication_snapshot())
            seen_products.add(item.product_id.id)
            if len(records) >= bounded:
                break
        return records

    @api.model
    def apply_quote_publication_plan(self, records, artifact_uri, artifact_sha256, confirmation, reason):
        records = list(records or [])
        if confirmation != QUOTE_PUBLICATION_CONFIRMATION or not (reason or "").strip():
            raise UserError(_("Quote-only publication requires explicit confirmation and a business reason."))
        if not records or len(records) > MAX_PROMOTION_BATCH:
            raise UserError(_("Quote-only publication plans must contain between 1 and 200 products."))
        if not (artifact_uri or "").startswith("s3://") or not SHA256_PATTERN.fullmatch(
            (artifact_sha256 or "").casefold()
        ):
            raise UserError(_("Quote-only publication requires an archived SHA-256 plan artifact."))
        results = []
        publication_fields = self._publication_fields()
        for prepared in records:
            item = self.sudo().browse(int(prepared.get("item_id") or 0)).exists()
            allowed_company_ids = self.env.user.company_ids.ids
            if (
                not item
                or item.company_id.id not in allowed_company_ids
                or item.company_id.id != int(prepared.get("company_id") or 0)
                or item.source_id.id != int(prepared.get("source_id") or 0)
            ):
                raise UserError(_("The prepared Sparex catalog item is unavailable."))
            self.env.cr.execute(
                "SELECT id FROM southern_vendor_catalog_item WHERE id = %s FOR UPDATE NOWAIT", [item.id]
            )
            item.invalidate_recordset()
            product = item.product_id.sudo()
            if not product:
                raise UserError(_("The prepared Sparex product is unavailable."))
            self.env.cr.execute("SELECT id FROM product_template WHERE id = %s FOR UPDATE NOWAIT", [product.id])
            product.invalidate_recordset()
            current = item._quote_publication_snapshot()
            if not item._quote_publication_eligible() or current != prepared:
                raise UserError(_("Sparex quote-only evidence changed; prepare a fresh publication plan."))
            expected_sha = _canonical_sha256({key: value for key, value in prepared.items() if key != "snapshot_sha256"})
            if expected_sha != (prepared.get("snapshot_sha256") or "").casefold():
                raise UserError(_("The quote-only publication snapshot checksum is invalid."))
            values = {
                "list_price": 0.0,
                "southern_quote_only": True,
                "southern_source_name": product.southern_source_name or item.source_id.name,
                "southern_source_url": prepared["source_url_after"],
                **prepared["descriptions_after"],
                **{name: True for name in publication_fields},
            }
            product.write(values)
            product.invalidate_recordset(list(values))
            blockers = sparex_publication_blockers(product, self.env["product.supplierinfo"])
            if (
                blockers
                or not product.website_published
                or not product.southern_quote_only
                or product.list_price != 0
                or not exact_sparex_url(product.southern_source_url, prepared["sku"])
                or not customer_description_ready(product)
                or not product.image_1920
                or not product.public_categ_ids
            ):
                rollback = {
                    "list_price": prepared["list_price_before"],
                    "southern_quote_only": prepared["quote_only_before"],
                    "southern_source_name": prepared["source_name_before"] or False,
                    "southern_source_url": prepared["source_url_before"] or False,
                    **prepared["descriptions_before"],
                    **prepared["publication_fields_before"],
                }
                product.write(rollback)
                raise UserError(_("Quote-only publication verification failed and the product was restored."))
            results.append(
                {
                    **prepared,
                    "artifact_uri": artifact_uri,
                    "artifact_sha256": artifact_sha256,
                    "public_path": product.website_url or f"/shop/product/{product.id}",
                }
            )
        return results

    @api.model
    def rollback_quote_publications(self, records, reason):
        if not (reason or "").strip():
            raise UserError(_("Quote-only publication rollback requires a reason."))
        publication_fields = set(self._publication_fields())
        for prepared in records or []:
            item = self.sudo().browse(int(prepared.get("item_id") or 0)).exists()
            product = item.product_id.sudo() if item else self.env["product.template"]
            if not product or product.id != int(prepared.get("product_id") or 0):
                continue
            self.env.cr.execute("SELECT id FROM product_template WHERE id = %s FOR UPDATE NOWAIT", [product.id])
            product.invalidate_recordset()
            if not product.southern_quote_only or product.list_price != 0:
                raise UserError(_("Quote-only rollback stopped because the product changed after publication."))
            product.write(
                {
                    "list_price": prepared.get("list_price_before") or 0.0,
                    "southern_quote_only": bool(prepared.get("quote_only_before")),
                    "southern_source_name": prepared.get("source_name_before") or False,
                    "southern_source_url": prepared.get("source_url_before") or False,
                    **prepared.get("descriptions_before", {}),
                    **{
                        name: bool(value)
                        for name, value in prepared.get("publication_fields_before", {}).items()
                        if name in publication_fields
                    },
                }
            )
        return True
