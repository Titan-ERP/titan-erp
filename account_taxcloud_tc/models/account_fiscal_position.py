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


from odoo import fields, models


class AccountFiscalPosition(models.Model):
    _inherit = ['account.fiscal.position', 'mail.thread', 'mail.activity.mixin']

    is_taxcloud_configured = fields.Boolean(
        related="company_id.is_taxcloud_configured",
        help="Used to determine whether or not to warn the user to configure TaxCloud.",
    )
    is_taxcloud = fields.Boolean(
        string="Use TaxCloud API",
        help="If enabled, this fiscal position will use the TaxCloud API "
        "to determine tax applicability and tax amounts. "
        "If disabled, it will use Odoo's standard tax mapping.",
        tracking=True,
    )
