from odoo import models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def _southern_queue_sparex_refresh(self):
        Queue = self.env["southern.parts.order.refresh.queue"].sudo()
        for order in self:
            products = order.order_line.product_id.product_tmpl_id
            Queue.enqueue_products(
                products,
                order,
                "purchase_order",
                refresh_cost=True,
                refresh_retail=True,
                refresh_source=True,
            )

    def button_confirm(self):
        result = super().button_confirm()
        self._southern_queue_sparex_refresh()
        return result

