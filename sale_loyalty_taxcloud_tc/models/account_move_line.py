from odoo import fields, models
from odoo.tools.sql import column_exists


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    reward_id = fields.Many2one(
        "loyalty.reward",
        string="Discount reward",
        readonly=True,
        help="The loyalty reward that created this line.",
    )
    # Technical field to hold prices for TaxCloud
    price_taxcloud = fields.Float("Taxcloud Price", default=0)
    is_reward_line = fields.Boolean(string="Is a program reward line")

    def _get_taxcloud_price(self):
        self.ensure_one()
        return self.price_taxcloud

    def _auto_init(self):
        if not column_exists(self.env.cr, "account_move_line", "price_taxcloud"):
            self.env.cr.execute(
                """
                    ALTER TABLE account_move_line
                    ADD COLUMN price_taxcloud DOUBLE PRECISION DEFAULT 0;
                """
            )
        return super()._auto_init()
