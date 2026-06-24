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

import logging
from odoo import api, fields, models
from odoo.exceptions import UserError

from .taxcloud_request import TaxCloudRequest

_logger = logging.getLogger(__name__)


class ProductTicCategory(models.Model):
    _name = "product.tic.category"
    _description = "Product TIC Category"
    _rec_name = "code"
    _rec_names_search = ["description", "code"]

    code = fields.Integer(string="TIC Category Code", required=True)
    description = fields.Char(string="TIC Description", required=True)

    @api.depends("code", "description")
    def _compute_display_name(self):
        for category in self:
            category.display_name = (
                f'[{category.code}] {(category.description or "")[:50]}'
            )

    @api.model
    def name_create(self, name):
        try:
            name = int(name)
        except ValueError as err:
            raise UserError(
                self.env._("The Taxcloud Category must be integer.")
            ) from err
        return super().name_create(name)


class ProductTemplate(models.Model):
    _inherit = "product.template"

    tic_category_id = fields.Many2one(
        "product.tic.category",
        string="TaxCloud Category",
        help="""This refers to TIC (Taxability Information Codes),"""
        """ these are used by TaxCloud to compute specific tax """
        """rates for each product type. The value set here prevails """
        """over the one set on the product category.""",
    )


class ResCompany(models.Model):
    _inherit = "res.company"

    taxcloud_api_id_v3 = fields.Char(string="TaxCloud Connection ID (V3)")
    taxcloud_api_key_v3 = fields.Char(string="TaxCloud API Key (V3)")
    taxcloud_api_version = fields.Selection(
        selection=[
            ('v3', 'V3 (REST)')
        ],
        string='TaxCloud API Version',
        default='v3',
        readonly=True,
        help='Select which version of the TaxCloud API to use.'
    )
    tic_category_id = fields.Many2one(
        "product.tic.category",
        string="Default TIC Code",
        help="TIC (Taxability Information Codes) allow "
        "to get specific tax rates for each product type. "
        "This default value applies if no product is used "
        "in the order/invoice, or if no TIC is set on "
        "the product or its product category. By default, "
        "TaxCloud relies on the TIC *[0] Uncategorized* "
        "default referring to general goods and services.",
    )
    is_taxcloud_configured = fields.Boolean(
        compute="_compute_is_taxcloud_configured",
        help="Used to determine whether or not to warn the user to configure TaxCloud.",
    )
    is_default_tax_template = fields.Boolean(string="Default Tax Template")
    tax_template_id = fields.Many2one(
        "account.tax", string="Default Tax", domain=[("type_tax_use", "=", "sale")]
    )
    is_skip_zero_invoice = fields.Boolean(string="Skip Zero Invoice")
    is_allow_cancel_invoice = fields.Boolean(string="Allow Cancel Invoice")

    @api.depends(
        "taxcloud_api_version",
        "taxcloud_api_id_v3",
        "taxcloud_api_key_v3"
    )
    def _compute_is_taxcloud_configured(self):
        for company in self:
            company.is_taxcloud_configured = bool(
                company.taxcloud_api_id_v3 and company.taxcloud_api_key_v3
            )

    @api.model
    def _cron_sync_taxcloud_tic_categories(self):
        """
        Scheduled action to fetch TIC categories daily for all configured companies.
        """
        # Find all companies that have TaxCloud set up
        companies = self.search([]).filtered('is_taxcloud_configured')

        for company in companies:
            api_version = company.taxcloud_api_version
            api_id = company.taxcloud_api_id_v3
            api_key = company.taxcloud_api_key_v3

            if not api_key:
                continue

            request = TaxCloudRequest(self.env, api_id, api_key, api_version)
            res = request.get_tic_category()

            if res.get("error_message"):
                _logger.error("TaxCloud TIC Cron Sync Error for %s: %s", company.name, res["error_message"])
                continue

            Category = self.env["product.tic.category"]
            existing_categories = Category.search([])
            existing_codes = set(existing_categories.mapped('code'))

            categories_to_create = []
            for category in res.get("data", []):
                tic_code = int(category["TICID"])
                if tic_code not in existing_codes:
                    categories_to_create.append({
                        "code": tic_code,
                        "description": category["Description"]
                    })
                    existing_codes.add(tic_code)
            if categories_to_create:
                Category.create(categories_to_create)
                _logger.info("TaxCloud Cron: Created %s new TIC categories for %s", len(categories_to_create), company.name)

            if not company.tic_category_id:
                company.tic_category_id = Category.search([("code", "=", 0)], limit=1)

class ProductCategory(models.Model):
    _inherit = "product.category"

    tic_category_id = fields.Many2one(
        "product.tic.category",
        string="TIC Code",
        help="This refers to TIC (Taxability Information Codes), "
        "these are used by TaxCloud to compute specific tax rates for "
        "each product type. This value is used when no TIC is set"
        " on the product. If no value is set here, the default "
        "value set in Invoicing settings is used.",
    )
