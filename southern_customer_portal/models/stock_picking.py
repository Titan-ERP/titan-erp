from odoo import models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def write(self, vals):
        result = super().write(vals)
        if "state" in vals:
            self._southern_advance_parts_orders_from_picking()
        return result

    def button_validate(self):
        result = super().button_validate()
        self._southern_advance_parts_orders_from_picking()
        return result

    def _southern_sale_orders_for_parts_review(self):
        orders = self.env["sale.order"].sudo()
        for picking in self:
            if picking.sale_id:
                orders |= picking.sale_id
            if picking.origin:
                orders |= orders.search([("name", "=", picking.origin)])
        return orders.filtered(
            lambda order: order.southern_parts_review_state not in ("not_parts", "completed")
        )

    def _southern_advance_parts_orders_from_picking(self):
        for picking in self:
            if picking.state not in ("assigned", "done"):
                continue
            picking_type = picking.picking_type_id.code
            for sale_order in picking._southern_sale_orders_for_parts_review():
                is_pickup = sale_order.carrier_id.name == sale_order.SOUTHERN_PICKUP_CARRIER
                is_reviewed_shipping = sale_order.carrier_id.name in (
                    sale_order.SOUTHERN_SHIP_CARRIER,
                    sale_order.SOUTHERN_DIRECT_SHIP_CARRIER,
                    *sale_order.SOUTHERN_LEGACY_SHIP_CARRIERS,
                )

                if picking_type == "incoming" and picking.state == "done":
                    if is_pickup:
                        sale_order._southern_set_parts_review_state(
                            "ready_for_pickup",
                            notify_customer=True,
                            internal_note=(
                                f"Automation marked parts ready for pickup after "
                                f"receipt {picking.name} was completed."
                            ),
                        )
                    else:
                        sale_order._southern_set_parts_review_state(
                            "confirmed",
                            notify_customer=True,
                            internal_note=(
                                f"Automation confirmed supplier receipt {picking.name} "
                                "for this parts order."
                            ),
                        )
                    continue

                if picking_type in ("outgoing", "dropship") and is_reviewed_shipping:
                    if picking.state == "assigned":
                        sale_order._southern_set_parts_review_state(
                            "shipping_in_progress",
                            notify_customer=True,
                            internal_note=(
                                f"Automation marked shipping in progress from delivery "
                                f"{picking.name}."
                            ),
                        )
                    elif picking.state == "done":
                        sale_order._southern_set_parts_review_state(
                            "completed",
                            notify_customer=True,
                            internal_note=(
                                f"Automation marked parts order complete after delivery "
                                f"{picking.name} was completed."
                            ),
                        )
        return True
