from odoo import models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def _southern_queue_sparex_refresh(self):
        Queue = self.env["southern.parts.order.refresh.queue"].sudo()
        for order in self:
            products = order.order_line.product_id.product_tmpl_id
            Queue.enqueue_products(
                products,
                order,
                "sale_order",
                refresh_cost=True,
                refresh_retail=True,
                refresh_source=True,
            )

    def action_confirm(self):
        result = super().action_confirm()
        self._southern_queue_sparex_refresh()
        return result

