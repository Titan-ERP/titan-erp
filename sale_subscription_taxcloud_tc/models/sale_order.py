# Copyright (c) 2015-2023 Odoo S.A.
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 3 of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with this program; if not, write to the Free Software Foundation,
# Inc., 51 Franklin Street, Fifth Floor, Boston, MA  02110-1301, USA.


from odoo import models


class SaleOrder(models.Model):
    """Ensure a correct invoice by validating taxcloud
    taxes in the subscription before invoice generation."""

    _inherit = "sale.order"

    def _do_payment(self, payment_token, invoice, auto_commit=False):
        if invoice.fiscal_position_id.is_taxcloud and invoice.move_type in [
            "out_invoice",
            "out_refund",
        ]:
            invoice.with_context(
                taxcloud_authorize_transaction=True
            ).validate_taxes_on_invoice()
        return super()._do_payment(payment_token, invoice, auto_commit=auto_commit)
