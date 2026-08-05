import json

from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ProductTemplate(models.Model):
    _inherit = "product.template"

    southern_specification_ids = fields.One2many(
        "southern.parts.specification",
        "product_tmpl_id",
        string="Part Specifications",
    )
    southern_fitment_ids = fields.One2many(
        "southern.parts.fitment",
        "product_tmpl_id",
        string="Make/Model Fitment",
    )
    southern_oem_reference_ids = fields.One2many(
        "southern.parts.oem_reference",
        "product_tmpl_id",
        string="OEM Cross References",
    )
    southern_catalog_page_ids = fields.One2many(
        "southern.parts.catalog_page",
        "product_tmpl_id",
        string="Catalog Pages",
    )
    southern_related_part_ids = fields.One2many(
        "southern.parts.related_product",
        "product_tmpl_id",
        string="Related Parts",
    )
    southern_alternate_barcode_ids = fields.One2many(
        "southern.parts.alternate_barcode",
        "product_tmpl_id",
        string="Alternate Barcodes",
    )
    southern_source_url = fields.Char(string="Source URL", index=True)
    southern_source_name = fields.Char(string="Source Name", index=True)
    southern_quote_only = fields.Boolean(
        string="Ask for Pricing",
        index=True,
        help="Publishes this evidence-complete product without a sales price and replaces purchasing controls with an Ask for Pricing action.",
    )
    southern_price_basis = fields.Selection(
        [
            ("none", "Not Set"),
            ("cost_plus", "Provisional Cost Plus"),
            ("retail_evidence", "Verified Retail Evidence"),
        ],
        default="none",
        required=True,
        index=True,
        copy=False,
        string="Sales Price Basis",
        help="Identifies whether the sales price is provisional cost-plus pricing or supported by reviewed retail evidence.",
    )
    southern_cost_plus_margin_percent = fields.Float(
        string="Cost Plus Gross Margin %",
        digits=(16, 2),
        readonly=True,
        copy=False,
    )
    southern_price_basis_updated_at = fields.Datetime(
        string="Sales Price Basis Updated At",
        readonly=True,
        copy=False,
    )
    southern_partner_price = fields.Float(
        string="Partner Price",
        digits="Product Price",
        company_dependent=True,
        help=(
            "Price charged to approved partner accounts such as diesel shops, "
            "parts stores, fleets, and volume buyers. This is separate from "
            "internal cost and public retail sales price."
        ),
    )
    southern_enrichment_status = fields.Selection(
        [
            ("none", "Not Enriched"),
            ("partial", "Partially Enriched"),
            ("complete", "Complete"),
            ("review", "Needs Review"),
        ],
        default="none",
        string="Parts Data Status",
        index=True,
    )
    southern_parts_search_text = fields.Text(
        string="Parts Search Text",
        compute="_compute_southern_parts_search_text",
        store=True,
        index="trigram",
        help="Aggregates OEM references, fitments, specifications, catalog pages, and alternate barcodes for parts-counter search.",
    )
    southern_specification_count = fields.Integer(
        string="Specification Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_fitment_count = fields.Integer(
        string="Fitment Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_fitment_make_count = fields.Integer(
        string="Fitment Make Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_fitment_model_count = fields.Integer(
        string="Fitment Model Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_oem_reference_count = fields.Integer(
        string="OEM Reference Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_catalog_page_count = fields.Integer(
        string="Catalog Page Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_related_part_count = fields.Integer(
        string="Related Part Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_alternate_barcode_count = fields.Integer(
        string="Alternate Barcode Count",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
    )
    southern_parts_catalog_snapshot_json = fields.Text(
        string="Website Parts Snapshot",
        compute="_compute_southern_parts_catalog_snapshot",
        store=True,
        help="Pre-rendered parts intelligence data used by the website to avoid loading every child record on product pages.",
    )
    southern_parts_snapshot_refreshed_at = fields.Datetime(
        string="Parts Snapshot Refreshed At",
        readonly=True,
    )

    @api.depends(
        "default_code",
        "barcode",
        "southern_specification_ids.name",
        "southern_specification_ids.value",
        "southern_fitment_ids.make_id.name",
        "southern_fitment_ids.model_id.name",
        "southern_fitment_ids.engine",
        "southern_fitment_ids.build_list",
        "southern_oem_reference_ids.manufacturer",
        "southern_oem_reference_ids.oem_part_number",
        "southern_catalog_page_ids.catalog_code",
        "southern_catalog_page_ids.catalog_name",
        "southern_catalog_page_ids.page_number",
        "southern_alternate_barcode_ids.barcode",
    )
    def _compute_southern_parts_search_text(self):
        for product in self:
            values = [
                product.default_code,
                product.barcode,
            ]
            for field_name in ("x_studio_manufacturer", "x_studio_oem_part_number", "x_studio_sub_reference"):
                if field_name in product._fields:
                    values.append(product[field_name])
            for spec in product.southern_specification_ids:
                values.extend([spec.name, spec.value, spec.unit])
            for fitment in product.southern_fitment_ids:
                values.extend([fitment.make_id.name, fitment.model_id.name, fitment.engine, fitment.build_list])
            for ref in product.southern_oem_reference_ids:
                values.extend([ref.manufacturer, ref.oem_part_number])
            for catalog in product.southern_catalog_page_ids:
                values.extend([catalog.catalog_code, catalog.catalog_name, catalog.page_number])
            for barcode in product.southern_alternate_barcode_ids:
                values.append(barcode.barcode)
            product.southern_parts_search_text = " ".join(str(value).strip() for value in values if value).strip()

    @api.model
    def _search_get_detail(self, website, order, options):
        detail = super()._search_get_detail(website, order, options)
        if "southern_parts_search_text" not in detail["search_fields"]:
            detail["search_fields"].append("southern_parts_search_text")
        if "southern_parts_search_text" not in detail["fetch_fields"]:
            detail["fetch_fields"].append("southern_parts_search_text")
        detail["mapping"]["southern_parts_search_text"] = {
            "name": "southern_parts_search_text",
            "type": "text",
            "match": True,
        }
        return detail

    @api.constrains("southern_partner_price")
    def _check_southern_partner_price(self):
        for product in self:
            if product.southern_partner_price < 0:
                raise ValidationError("Partner Price cannot be negative.")

    @api.constrains("southern_quote_only", "list_price")
    def _check_southern_quote_only_price(self):
        for product in self:
            if product.southern_quote_only and product.list_price > 0:
                raise ValidationError("Ask for Pricing products must have a zero sales price.")

    @api.model_create_multi
    def create(self, vals_list):
        partner_prices = []
        for vals in vals_list:
            partner_prices.append(vals.pop("southern_partner_price", False))
        products = super().create(vals_list)
        for product, partner_price in zip(products, partner_prices):
            if partner_price:
                product.southern_partner_price = partner_price
        products.filtered(
            lambda product: product.southern_partner_price > 0
        )._southern_sync_partner_pricing()
        return products

    def write(self, vals):
        result = super().write(vals)
        if "southern_partner_price" in vals:
            self._southern_sync_partner_pricing()
        return result

    def _southern_partner_pricelist(self):
        pricelist = self.env.ref(
            "southern_parts_intelligence.southern_partner_pricelist",
            raise_if_not_found=False,
        )
        if pricelist:
            return pricelist
        return self.env["product.pricelist"].sudo().search(
            [("name", "=", "Southern Partner Pricing")],
            limit=1,
        )

    def _southern_sync_partner_pricing(self):
        pricelist = self._southern_partner_pricelist()
        if not pricelist:
            return

        PricelistItem = self.env["product.pricelist.item"].sudo()
        for product in self.sudo():
            item = PricelistItem.search(
                [
                    ("pricelist_id", "=", pricelist.id),
                    ("applied_on", "=", "1_product"),
                    ("product_tmpl_id", "=", product.id),
                ],
                limit=1,
            )
            if product.southern_partner_price > 0:
                vals = {
                    "pricelist_id": pricelist.id,
                    "applied_on": "1_product",
                    "product_tmpl_id": product.id,
                    "compute_price": "fixed",
                    "fixed_price": product.southern_partner_price,
                }
                if item:
                    item.write(vals)
                else:
                    PricelistItem.create(vals)
            elif item:
                item.unlink()

    @api.depends(
        "default_code",
        "barcode",
        "southern_specification_ids.sequence",
        "southern_specification_ids.group_name",
        "southern_specification_ids.name",
        "southern_specification_ids.value",
        "southern_specification_ids.unit",
        "southern_fitment_ids.make_id.name",
        "southern_fitment_ids.model_id.name",
        "southern_fitment_ids.engine",
        "southern_fitment_ids.year_from",
        "southern_fitment_ids.year_to",
        "southern_fitment_ids.build_list",
        "southern_fitment_ids.notes",
        "southern_oem_reference_ids.manufacturer",
        "southern_oem_reference_ids.oem_part_number",
        "southern_catalog_page_ids.catalog_code",
        "southern_catalog_page_ids.catalog_name",
        "southern_catalog_page_ids.page_number",
        "southern_catalog_page_ids.source_url",
        "southern_related_part_ids.relationship_type",
        "southern_related_part_ids.related_product_tmpl_id.name",
        "southern_related_part_ids.related_product_tmpl_id.default_code",
        "southern_alternate_barcode_ids.barcode",
    )
    def _compute_southern_parts_catalog_snapshot(self):
        for product in self:
            snapshot = product._build_southern_parts_catalog_snapshot()
            counts = snapshot.get("counts", {})
            product.southern_specification_count = counts.get("specifications", 0)
            product.southern_fitment_count = counts.get("fitments", 0)
            product.southern_fitment_make_count = counts.get("fitment_makes", 0)
            product.southern_fitment_model_count = counts.get("fitment_models", 0)
            product.southern_oem_reference_count = counts.get("oem_references", 0)
            product.southern_catalog_page_count = counts.get("catalog_pages", 0)
            product.southern_related_part_count = counts.get("related_parts", 0)
            product.southern_alternate_barcode_count = counts.get("alternate_barcodes", 0)
            product.southern_parts_catalog_snapshot_json = json.dumps(snapshot, sort_keys=True)

    def _build_southern_parts_catalog_snapshot(self):
        self.ensure_one()
        product = self.sudo()
        sections = []
        specifications = product._southern_grouped_specifications_from_records()
        fitments = product._southern_grouped_fitments_from_records()
        oem_references = product._southern_grouped_oem_references_from_records()
        catalog_pages = product._southern_grouped_catalog_pages_from_records()
        related_parts = product._southern_related_parts_from_records()
        if specifications:
            sections.append("specifications")
        if fitments:
            sections.append("fitment")
        if oem_references:
            sections.append("oem")
        if catalog_pages:
            sections.append("catalog")
        if related_parts:
            sections.append("related")
        counts = {
            "specifications": len(product.southern_specification_ids),
            "fitments": len(product.southern_fitment_ids),
            "fitment_makes": len(product.southern_fitment_ids.mapped("make_id")),
            "fitment_models": len(product.southern_fitment_ids.mapped("model_id")),
            "oem_references": len(product.southern_oem_reference_ids),
            "catalog_pages": len(product.southern_catalog_page_ids),
            "related_parts": len(product.southern_related_part_ids),
            "alternate_barcodes": len(product.southern_alternate_barcode_ids),
        }
        quick_facts = product._southern_website_quick_facts_from_counts(counts)
        return {
            "sections": sections,
            "counts": counts,
            "quick_facts": quick_facts,
            "specifications": specifications,
            "fitments": fitments,
            "oem_references": oem_references,
            "catalog_pages": catalog_pages,
            "related_parts": related_parts,
        }

    def _southern_parts_catalog_snapshot(self):
        self.ensure_one()
        raw = self.southern_parts_catalog_snapshot_json
        if raw:
            try:
                return json.loads(raw)
            except (TypeError, ValueError):
                pass
        return self.sudo()._build_southern_parts_catalog_snapshot()

    def _southern_has_parts_catalog_data(self):
        self.ensure_one()
        return bool(
            self.southern_specification_count
            or self.southern_fitment_count
            or self.southern_oem_reference_count
            or self.southern_catalog_page_count
            or self.southern_related_part_count
            or self.southern_alternate_barcode_count
        )

    def _southern_website_catalog_sections(self):
        self.ensure_one()
        return self._southern_parts_catalog_snapshot().get("sections", [])

    def _southern_grouped_specifications(self):
        self.ensure_one()
        return self._southern_parts_catalog_snapshot().get("specifications", [])

    def _southern_grouped_specifications_from_records(self):
        self.ensure_one()
        grouped = {}
        for spec in self.sudo().southern_specification_ids:
            group_name = spec.group_name or "Specifications"
            grouped.setdefault(group_name, []).append(
                {
                    "name": spec.name,
                    "value": spec.value,
                    "unit": spec.unit,
                    "source_name": spec.source_name,
                }
            )
        return [{"group": group, "items": items} for group, items in grouped.items()]

    def _southern_grouped_fitments(self):
        self.ensure_one()
        return self._southern_parts_catalog_snapshot().get("fitments", [])

    def _southern_grouped_fitments_from_records(self):
        self.ensure_one()
        grouped = {}
        for fitment in self.sudo().southern_fitment_ids:
            make_name = fitment.make_id.name or "Other"
            grouped.setdefault(make_name, []).append(
                {
                    "model": fitment.model_id.name,
                    "engine": fitment.engine,
                    "year_from": fitment.year_from,
                    "year_to": fitment.year_to,
                    "build_list": fitment.build_list,
                    "notes": fitment.notes,
                }
            )
        return [{"make": make, "items": items} for make, items in grouped.items()]

    def _southern_grouped_oem_references(self):
        self.ensure_one()
        return self._southern_parts_catalog_snapshot().get("oem_references", [])

    def _southern_grouped_oem_references_from_records(self):
        self.ensure_one()
        grouped = {}
        for ref in self.sudo().southern_oem_reference_ids:
            manufacturer = ref.manufacturer or "Other"
            grouped.setdefault(manufacturer, []).append(ref.oem_part_number)
        return [{"manufacturer": manufacturer, "numbers": numbers} for manufacturer, numbers in grouped.items()]

    def _southern_grouped_catalog_pages(self):
        self.ensure_one()
        return self._southern_parts_catalog_snapshot().get("catalog_pages", [])

    def _southern_grouped_catalog_pages_from_records(self):
        self.ensure_one()
        grouped = {}
        for catalog in self.sudo().southern_catalog_page_ids:
            catalog_name = catalog.catalog_name or catalog.catalog_code or "Catalog"
            grouped.setdefault(catalog_name, {"code": catalog.catalog_code, "pages": []})
            grouped[catalog_name]["pages"].append(
                {
                    "page_number": catalog.page_number,
                    "source_url": catalog.source_url,
                }
            )
        return [{"catalog": catalog, "code": data["code"], "pages": data["pages"]} for catalog, data in grouped.items()]

    def _southern_website_quick_facts(self):
        self.ensure_one()
        return self._southern_parts_catalog_snapshot().get("quick_facts", [])

    def _southern_website_quick_facts_from_counts(self, counts):
        self.ensure_one()
        product = self.sudo()
        facts = []
        if product.default_code:
            facts.append({"label": "SKU", "value": product.default_code})
        if product.barcode:
            facts.append({"label": "Barcode", "value": product.barcode})
        for label, field_name in (
            ("Manufacturer", "x_studio_manufacturer"),
            ("OEM Part Number", "x_studio_oem_part_number"),
            ("Sub Reference", "x_studio_sub_reference"),
        ):
            if field_name in product._fields and product[field_name]:
                facts.append({"label": label, "value": product[field_name]})
        if counts.get("oem_references"):
            facts.append({"label": "OEM Cross References", "value": counts["oem_references"]})
        if counts.get("fitments"):
            facts.append({"label": "Fits", "value": f"{counts.get('fitment_makes', 0)} makes / {counts.get('fitment_models', 0)} models"})
        if counts.get("catalog_pages"):
            facts.append({"label": "Catalog References", "value": counts["catalog_pages"]})
        return facts

    def _southern_related_parts_summary(self):
        self.ensure_one()
        return self._southern_parts_catalog_snapshot().get("related_parts", [])

    def _southern_related_parts_from_records(self):
        self.ensure_one()
        parts = []
        for related in self.sudo().southern_related_part_ids:
            related_product = related.related_product_tmpl_id
            parts.append(
                {
                    "relationship_type": dict(related._fields["relationship_type"].selection).get(related.relationship_type, related.relationship_type),
                    "name": related_product.display_name,
                    "default_code": related_product.default_code,
                    "website_url": related_product.website_url or "#",
                }
            )
        return parts
