import hashlib
import json
import re
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


def _canonical_sha256(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _normalized_sku(value):
    return re.sub(r"\s+", "", (value or "").strip()).upper()[:128]


def _https_url(value):
    parsed = urlsplit((value or "").strip())
    return parsed.scheme.casefold() == "https" and bool(parsed.hostname)


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
    def _readiness(self, source, values, match_state):
        if match_state == "duplicate":
            return "blocked", "duplicate_product"
        if match_state == "matched":
            return "promoted", False
        if not source.partner_id:
            return "staged", "missing_vendor"
        if not values.get("title"):
            return "staged", "missing_title"
        if not _https_url(values.get("source_url")):
            return "staged", "missing_source_url"
        if not _https_url(values.get("image_url")):
            return "staged", "missing_image"
        if float(values.get("vendor_cost") or 0) <= 0:
            return "staged", "missing_cost"
        if float(values.get("sales_price") or 0) <= 0:
            return "staged", "missing_sales_price"
        if float(values.get("sales_price") or 0) <= float(values.get("vendor_cost") or 0):
            return "blocked", "price_below_cost"
        if not source.default_category_id:
            return "staged", "missing_category"
        return "ready", False

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
        now = fields.Datetime.now()
        normalized_records = {}
        for record in records:
            record = dict(record or {})
            vendor_sku = (record.get("vendor_sku") or record.get("sku") or "").strip()[:128]
            normalized = _normalized_sku(record.get("normalized_sku") or vendor_sku)
            title = " ".join((record.get("title") or record.get("listing_title") or "").split()).strip()[:255]
            source_url = (record.get("source_url") or "").strip()
            image_url = (record.get("image_url") or "").strip()
            if not vendor_sku or not normalized or normalized in normalized_records:
                raise UserError(_("Each catalog batch item must contain one unique vendor SKU."))
            if not title or not _https_url(source_url) or (image_url and not _https_url(image_url)):
                raise UserError(_("Catalog items require a title, HTTPS source URL, and optional HTTPS image URL."))
            prefix = (source.internal_reference_prefix or "").strip()
            internal_reference = f"{prefix}{normalized}"[:128]
            canonical = {
                "vendor_sku": vendor_sku,
                "normalized_sku": normalized,
                "internal_reference": internal_reference,
                "title": title,
                "customer_description": (record.get("customer_description") or "").strip(),
                "category_path": (record.get("category_path") or "").strip()[:255],
                "source_url": source_url,
                "image_url": image_url,
                "vendor_cost": max(0.0, float(record.get("vendor_cost") or 0)),
                "sales_price": max(0.0, float(record.get("sales_price") or 0)),
                "availability": record.get("availability") if record.get("availability") in {"unknown", "available", "backorder", "discontinued"} else "unknown",
            }
            canonical["content_sha256"] = _canonical_sha256(canonical)
            normalized_records[normalized] = canonical
        existing = self.sudo().search(
            [("source_id", "=", source.id), ("normalized_sku", "in", list(normalized_records))]
        )
        existing_by_sku = {item.normalized_sku: item for item in existing}
        created = updated = unchanged = ready = 0
        create_values = []
        for normalized, values in normalized_records.items():
            item = existing_by_sku.get(normalized)
            match_state, product = self._match_product(values["internal_reference"])
            promotion_state, blocker_code = self._readiness(source, values, match_state)
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
                "active": values["availability"] != "discontinued",
            }
            if item:
                write_values["observation_count"] = item.observation_count + 1
                if item.content_sha256 == values["content_sha256"] and item.source_artifact_sha256 == artifact_sha256:
                    item.write(write_values)
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
                    }
                )
            item.write(
                {
                    "product_id": product.id,
                    "match_state": "matched",
                    "promotion_state": "promoted",
                    "blocker_code": False,
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
        customer_copy = self.customer_description or (
            f"{self.title}. Contact Southern Equipment for current pricing, fitment, and availability."
        )
        descriptions_after = dict(descriptions)
        if not customer_description_ready(product):
            descriptions_after = {field_name: customer_copy for field_name in descriptions}
        source_url_after = product.southern_source_url or self.source_url
        if not exact_sparex_url(source_url_after, normalized):
            source_url_after = self.source_url
        snapshot = {
            "item_id": self.id,
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

    def _quote_publication_eligible(self):
        self.ensure_one()
        product = self.product_id.sudo()
        normalized = normalized_sparex_sku(product.default_code)
        return bool(
            self.active
            and self.source_id.code == "sparex"
            and self.match_state == "matched"
            and product
            and normalized
            and exact_sparex_url(self.source_url, normalized)
            and self.source_artifact_uri.startswith("s3://")
            and SHA256_PATTERN.fullmatch((self.source_artifact_sha256 or "").casefold())
            and product.active
            and product.sale_ok
            and not product.website_published
            and float(product.list_price or 0.0) <= 1.49
            and bool(product.image_1920)
            and bool(product.public_categ_ids)
            and bool(self.title)
        )

    @api.model
    def prepare_quote_publication_plan(self, limit=MAX_PROMOTION_BATCH):
        bounded = max(1, min(int(limit or MAX_PROMOTION_BATCH), MAX_PROMOTION_BATCH))
        items = self.sudo().search(
            [
                ("company_id", "=", self.env.company.id),
                ("source_id.code", "=", "sparex"),
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
            if not item or item.company_id != self.env.company:
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
