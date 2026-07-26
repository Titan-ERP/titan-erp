from odoo import fields, models


class SouthernPartsSpecification(models.Model):
    _name = "southern.parts.specification"
    _description = "Southern Parts Specification"
    _order = "sequence, group_name, name, id"

    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    group_name = fields.Char(default="Specifications", index=True)
    name = fields.Char(required=True, index=True)
    value = fields.Char(required=True, index=True)
    unit = fields.Char()
    source_name = fields.Char(index=True)
    source_url = fields.Char()
    confidence = fields.Float(default=1.0)
    notes = fields.Text()


class SouthernPartsMake(models.Model):
    _name = "southern.parts.make"
    _description = "Southern Equipment Make"
    _order = "name"

    name = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("name_unique", "unique(name)", "Make must be unique."),
    ]


class SouthernPartsModel(models.Model):
    _name = "southern.parts.model"
    _description = "Southern Equipment Model"
    _order = "make_id, name"

    name = fields.Char(required=True, index=True)
    make_id = fields.Many2one("southern.parts.make", required=True, ondelete="cascade", index=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ("make_model_unique", "unique(make_id, name)", "Model must be unique per make."),
    ]


class SouthernPartsFitment(models.Model):
    _name = "southern.parts.fitment"
    _description = "Southern Parts Make/Model Fitment"
    _order = "make_id, model_id, id"

    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    make_id = fields.Many2one("southern.parts.make", required=True, ondelete="restrict", index=True)
    model_id = fields.Many2one("southern.parts.model", required=True, ondelete="restrict", index=True)
    engine = fields.Char(index=True)
    year_from = fields.Integer()
    year_to = fields.Integer()
    build_list = fields.Char(index=True)
    notes = fields.Text()
    source_name = fields.Char(index=True)
    source_url = fields.Char()
    confidence = fields.Float(default=1.0)


class SouthernPartsOemReference(models.Model):
    _name = "southern.parts.oem_reference"
    _description = "Southern Parts OEM Cross Reference"
    _order = "manufacturer, oem_part_number, id"

    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    manufacturer = fields.Char(required=True, index=True)
    oem_part_number = fields.Char(required=True, index=True)
    reference_type = fields.Selection(
        [
            ("oem", "OEM"),
            ("replacement", "Replacement"),
            ("alternate", "Alternate"),
            ("supersession", "Supersession"),
            ("supplier", "Supplier"),
        ],
        default="oem",
        required=True,
        index=True,
    )
    source_name = fields.Char(index=True)
    source_url = fields.Char()
    confidence = fields.Float(default=1.0)
    notes = fields.Text()


class SouthernPartsCatalogPage(models.Model):
    _name = "southern.parts.catalog_page"
    _description = "Southern Parts Catalog Page Reference"
    _order = "catalog_name, page_number, id"

    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    catalog_code = fields.Char(index=True)
    catalog_name = fields.Char(required=True, index=True)
    page_number = fields.Char(required=True, index=True)
    source_name = fields.Char(index=True)
    source_url = fields.Char()
    notes = fields.Text()


class SouthernPartsRelatedProduct(models.Model):
    _name = "southern.parts.related_product"
    _description = "Southern Typed Related Part"
    _order = "relationship_type, related_product_tmpl_id"

    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    related_product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    relationship_type = fields.Selection(
        [
            ("related", "Related"),
            ("alternate", "Alternate"),
            ("replacement", "Replacement"),
            ("replaces", "Replaces"),
            ("replaced_by", "Replaced By"),
            ("kit_component", "Kit Component"),
            ("accessory", "Accessory"),
        ],
        default="related",
        required=True,
        index=True,
    )
    source_name = fields.Char(index=True)
    source_url = fields.Char()
    confidence = fields.Float(default=1.0)
    notes = fields.Text()


class SouthernPartsAlternateBarcode(models.Model):
    _name = "southern.parts.alternate_barcode"
    _description = "Southern Parts Alternate Barcode"
    _order = "barcode"

    product_tmpl_id = fields.Many2one("product.template", required=True, ondelete="cascade", index=True)
    barcode = fields.Char(required=True, index=True)
    barcode_type = fields.Selection(
        [
            ("upc", "UPC"),
            ("ean13", "EAN-13"),
            ("code128", "Code 128"),
            ("supplier", "Supplier"),
            ("other", "Other"),
        ],
        default="other",
        required=True,
        index=True,
    )
    source_name = fields.Char(index=True)
    source_url = fields.Char()

    _sql_constraints = [
        ("product_barcode_unique", "unique(product_tmpl_id, barcode)", "Alternate barcode must be unique per product."),
    ]
