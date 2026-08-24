from odoo import _, models
from odoo.exceptions import AccessError, UserError


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    def action_southern_reset_to_draft(self):
        if not self.env.user.has_group("purchase.group_purchase_user"):
            raise AccessError(_("Purchase access is required to reset a purchase order."))

        unsupported = self.filtered(
            lambda order: order.state not in ("draft", "sent", "to approve", "purchase", "cancel")
        )
        if unsupported:
            raise UserError(
                _(
                    "The following purchase orders cannot be reset to draft: %s",
                    ", ".join(unsupported.mapped("display_name")),
                )
            )

        for order in self:
            if order.state in ("sent", "to approve", "purchase"):
                order.button_cancel()
            if order.state == "cancel":
                order.button_draft()
        return True


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_southern_reset_to_draft(self):
        if not self.env.user.has_group("sales_team.group_sale_salesman"):
            raise AccessError(_("Sales access is required to reset a sales order."))

        unsupported = self.filtered(
            lambda order: order.state not in ("draft", "sent", "sale", "cancel")
        )
        if unsupported:
            raise UserError(
                _(
                    "The following sales orders cannot be reset to draft: %s",
                    ", ".join(unsupported.mapped("display_name")),
                )
            )

        for order in self:
            if order.state == "sale":
                order.action_cancel()
            if order.state in ("sent", "cancel"):
                order.action_draft()
        return True
