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


from odoo.addons.account_taxcloud_tc.models import taxcloud_request


class TaxCloudRequest(taxcloud_request.TaxCloudRequest):
    def set_order_items_detail(self, order):
        self.customer_id = order.partner_invoice_id.id
        self.cart_id = order.id
        self.cart_items = self._process_lines(order.order_line)

