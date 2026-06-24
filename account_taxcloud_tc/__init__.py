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

from . import models

from odoo.exceptions import UserError
from odoo import _


def pre_init_hook(env):
    module_obj = env["ir.module.module"]
    unavailable_tc_modules = []
    odoo_taxcloud_modules = [
        "sale_account_taxcloud",
        "sale_amazon_taxcloud",
        "sale_loyalty_taxcloud",
        "sale_loyalty_taxcloud_delivery",
        "sale_subscription_taxcloud",
        "website_sale_account_taxcloud",
    ]
    installed_taxcloud_modules = module_obj.search(
        [("name", "in", odoo_taxcloud_modules), ("state", "=", "installed")]
    ).mapped("name")
    for installed_module_name in installed_taxcloud_modules:
        tc_module_name = f"{installed_module_name}_tc"
        if not module_obj.search([("name", "=", tc_module_name)]):
            unavailable_tc_modules.append(tc_module_name)
    if unavailable_tc_modules:
        raise UserError(
            env._(
                """Odoo TaxCloud modules\n\n%s\nare in installed state.
                \n\nBut corresponding TaxCloud Official modules\n\n%s\
                \nare not available in your system.
                \n\nPlease download from Odoo Apps Store to proceed with the installation"""
            )
            % (
                "\n".join(
                    [
                        installed_taxcloud_module
                        for installed_taxcloud_module in installed_taxcloud_modules
                    ]
                ),
                "\n".join(
                    [
                        unavailable_tc_module
                        for unavailable_tc_module in unavailable_tc_modules
                    ]
                ),
            )
        )

def post_init_hook(env):
    """
    Forces the 'Round per Line' setting on all companies when the module is installed,
    as required by TaxCloud for accurate line-by-line tax computation.
    """
    companies = env['res.company'].search([])
    companies.write({'tax_calculation_rounding_method': 'round_per_line'})
