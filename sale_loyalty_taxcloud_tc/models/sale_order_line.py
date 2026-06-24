from odoo import api, fields, models
from odoo.exceptions import UserError
from odoo.tools.sql import column_exists


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # Technical fields to hold prices for TaxCloud
    price_taxcloud = fields.Float("Taxcloud Price", default=0)

    def _check_taxcloud_promo(self, vals):
        """Ensure that users cannot modify sale order lines of a Taxcloud order
        with promotions if there is already a valid invoice"""

        blocked_fields = (
            "product_id",
            "price_unit",
            "price_subtotal",
            "price_tax",
            "price_total",
#            "tax_ids",
            "discount",
            "product_uom_qty",
            "product_qty",
        )
        for line in self:
            if line.is_reward_line or vals.get('reward_id') or vals.get('reward_identifier_code'):
                continue
            has_posted_invoice = any(line.order_id.invoice_ids.filtered(lambda inv: inv.state == 'posted'))
            has_pos_invoice = False
            if hasattr(line, 'pos_order_line_ids') and line.pos_order_line_ids:
                has_pos_invoice = any(
                    pos_line.order_id.account_move.state == 'posted'
                    for pos_line in line.pos_order_line_ids
                    if pos_line.order_id.account_move
                )
            if (
                line.order_id.is_taxcloud
                and not line.display_type
                and any(field in vals for field in blocked_fields)
                and (has_posted_invoice or has_pos_invoice)
                and any(line.order_id.order_line.mapped("is_reward_line"))
            ):
                raise UserError(
                    self.env._(
                        "Orders with coupons or promotions programs that use TaxCloud for "
                        "automatic tax computation cannot be modified after having been "
                        "invoiced.\nTo modify this order, you must first cancel "
                        "or refund all existing invoices."
                    )
                )

    def write(self, vals):
        self._check_taxcloud_promo(vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        for line, vals in zip(lines, vals_list):
            line._check_taxcloud_promo(vals)
        return lines

    def _get_taxcloud_price(self):
        self.ensure_one()
        return self.price_taxcloud

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        res.update(reward_id=self.reward_id.id, is_reward_line=self.is_reward_line)
        return res

    def _auto_init(self):
        if not column_exists(self.env.cr, "sale_order_line", "price_taxcloud"):
            self.env.cr.execute(
                """
                    ALTER TABLE sale_order_line
                    ADD COLUMN price_taxcloud DOUBLE PRECISION DEFAULT 0;
                """
            )
        return super()._auto_init()
