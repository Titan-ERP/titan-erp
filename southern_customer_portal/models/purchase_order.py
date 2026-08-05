from odoo import models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def button_confirm(self):
        result = super().button_confirm()
        self._southern_advance_linked_parts_orders_from_purchase()
        return result

    def write(self, vals):
        result = super().write(vals)
        if "state" in vals:
            self._southern_advance_linked_parts_orders_from_purchase()
        return result

    def _southern_linked_parts_sale_orders(self):
        SaleOrder = self.env["sale.order"].sudo()
        linked_orders = SaleOrder
        for purchase_order in self:
            sale_orders = SaleOrder
            if "sale_line_id" in purchase_order.order_line._fields:
                sale_orders = purchase_order.order_line.mapped("sale_line_id.order_id")
            if not sale_orders and purchase_order.origin:
                sale_orders = SaleOrder.search([("name", "=", purchase_order.origin)])
            linked_orders |= sale_orders.filtered(
                lambda order: order.southern_parts_review_state
                not in ("not_parts", "completed")
            )
        return linked_orders

    def _southern_advance_linked_parts_orders_from_purchase(self):
        for purchase_order in self.filtered(lambda order: order.state in ("purchase", "done")):
            for sale_order in purchase_order._southern_linked_parts_sale_orders():
                if sale_order.southern_parts_review_state in ("parts_review", "supplier_verification"):
                    sale_order._southern_set_parts_review_state(
                        "supplier_verification",
                        notify_customer=True,
                        internal_note=(
                            "Automation confirmed vendor purchasing is active for "
                            f"{purchase_order.name}."
                        ),
                    )
        return True
