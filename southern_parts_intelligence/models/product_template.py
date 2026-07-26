from odoo import api, fields, models


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
        help="Aggregates OEM references, fitments, specifications, catalog pages, and alternate barcodes for parts-counter search.",
    )

    @api.depends(
        "default_code",
        "barcode",
        "x_studio_manufacturer",
        "x_studio_oem_part_number",
        "x_studio_sub_reference",
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

    def _southern_has_parts_catalog_data(self):
        self.ensure_one()
        product = self.sudo()
        return bool(
            product.southern_specification_ids
            or product.southern_fitment_ids
            or product.southern_oem_reference_ids
            or product.southern_catalog_page_ids
            or product.southern_related_part_ids
            or product.southern_alternate_barcode_ids
        )

    def _southern_website_catalog_sections(self):
        self.ensure_one()
        product = self.sudo()
        sections = []
        if product.southern_specification_ids:
            sections.append("specifications")
        if product.southern_fitment_ids:
            sections.append("fitment")
        if product.southern_oem_reference_ids:
            sections.append("oem")
        if product.southern_catalog_page_ids:
            sections.append("catalog")
        if product.southern_related_part_ids:
            sections.append("related")
        return sections

    def _southern_grouped_specifications(self):
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
        grouped = {}
        for ref in self.sudo().southern_oem_reference_ids:
            manufacturer = ref.manufacturer or "Other"
            grouped.setdefault(manufacturer, []).append(ref.oem_part_number)
        return [{"manufacturer": manufacturer, "numbers": numbers} for manufacturer, numbers in grouped.items()]

    def _southern_grouped_catalog_pages(self):
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
