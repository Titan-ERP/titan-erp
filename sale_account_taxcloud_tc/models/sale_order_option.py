from odoo import models


class SaleOrderLine(models.Model):
    """Override SaleOrderLine to integrate TaxCloud when optional products are added.

    In Odoo v19.0, the sale.order.option model was removed and replaced with
    the is_optional boolean field on sale.order.line. Optional products are identified
    by the is_optional flag on section lines, and checked via _is_line_optional() method.
    """
    _inherit = "sale.order.line"

    def write(self, values):
        """Override write to recalculate taxes when optional product quantities change."""
        res = super().write(values)

        # If product_uom_qty was updated on optional lines, recalculate taxes
        if 'product_uom_qty' in values:
            for line in self.filtered(lambda l: l.order_id.is_taxcloud and l._is_line_optional()):
                line.order_id.validate_taxes_on_sales_order()

        return res
