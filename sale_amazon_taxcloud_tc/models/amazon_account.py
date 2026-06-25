from odoo import models


class AmazonAccount(models.Model):
    _inherit = "amazon.account"

    def _recompute_subtotal(
        self, subtotal, tax_amount, taxes, currency, fiscal_pos=None
    ):
        """Bypass the recomputation of the subtotal and let TaxCloud fetch the right taxes."""
        if fiscal_pos and fiscal_pos.is_taxcloud:
            return subtotal
        else:
            return super()._recompute_subtotal(
                subtotal, tax_amount, taxes, currency, fiscal_pos=fiscal_pos
            )

    def _create_order_from_data(self, order_data):
        """Override to let TaxCloud set the right taxes when creating orders from the SP-API."""
        order = super()._create_order_from_data(order_data)
        if (
            order.fiscal_position_id.is_taxcloud
            and not self.env.company.is_dont_compute_tax_for_amazon_orders
        ):
            was_locked = True if order.locked else False
            if was_locked:
                order.with_context(mail_notrack=True).write({"locked": False})
            order.validate_taxes_on_sales_order()
            if was_locked:
                order.with_context(mail_notrack=True).write({"locked": True})
        return order
