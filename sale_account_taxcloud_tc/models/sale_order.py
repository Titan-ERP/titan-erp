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


import datetime
import logging
from urllib import request

from odoo import SUPERUSER_ID, api, fields, models, Command
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round, ormcache

from .taxcloud_request import TaxCloudRequest

_logger = logging.getLogger(__name__)


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # Used to determine whether or not to warn the user to configure TaxCloud
    is_taxcloud_configured = fields.Boolean(related="company_id.is_taxcloud_configured")
    # Technical field to determine whether to hide taxes in views or not
    is_taxcloud = fields.Boolean(related="fiscal_position_id.is_taxcloud")
    total_tax_amount_tc = fields.Float("TaxCloud Total Tax")

    def action_quotation_send(self):
        self.validate_taxes_on_sales_order()
        return super().action_quotation_send()

    def action_quotation_sent(self):
        for order in self:
            order.validate_taxes_on_sales_order()
        return super().action_quotation_sent()

    @api.model
    def _get_TaxCloudRequest(self, api_id, api_key, api_version):
        return TaxCloudRequest(self.env, api_id, api_key, api_version)

    @api.model
    @ormcache("request_hash")
    def _get_all_taxes_values(self, request, request_hash):
        return request.get_all_taxes_values()

    # Used to prepare the taxcloud request
    # So that we can inherit this method in another modules to update the request.
    def prepare_taxcloud_request(self):
        shipper = self.company_id or self.env.company
        api_version = shipper.taxcloud_api_version

        api_id = (shipper.taxcloud_api_id_v3 or "").strip()
        api_key = (shipper.taxcloud_api_key_v3 or "").strip()

        request = self._get_TaxCloudRequest(api_id, api_key, api_version)
        request.set_location_origin_detail(shipper)
        request.set_location_destination_detail(self.partner_shipping_id)
        request.set_order_items_detail(self)
        return request

    def validate_taxes_on_sales_order(self):
        if not self.fiscal_position_id.is_taxcloud:
            return True
        company = self.company_id
        request = self.prepare_taxcloud_request()

        if company.tax_calculation_rounding_method != 'round_per_line':
            raise ValidationError(
                self.env._(
                    "TaxCloud requires line-by-line rounding to match state jurisdictions accurately.\n"
                    "Please go to Invoicing/Accounting Settings and change the 'Rounding Method' "
                    "to 'Round per Line'."
                )
            )
        if isinstance(request.cart_items, list):
            cart_items = request.cart_items
        else:
            cart_items = request.cart_items.CartItem

        if (
            not cart_items
            and len(
                self.order_line.filtered(
                    lambda x: x.display_type not in ("line_note", "line_section")
                )
            )
            and self.env.company.is_skip_zero_orders
        ):
            return True
        request.taxcloud_date = fields.Datetime.context_timestamp(
            self, datetime.datetime.now()
        )
        if (
            not cart_items
            and "fsm_mode" in self.env.context
            and self.env.context.get("fsm_mode")
        ):
            return True
        response = self._get_all_taxes_values(request, request.hash)

        self._check_taxcloud_response(response)
        if response.get("error_message"):
            raise ValidationError(
                self.env._("Unable to retrieve taxes from TaxCloud: ")
                + "\n"
                + response["error_message"]
            )

        tax_values = response.get("values", {})
        if tax_values:
            # FIX 1: Safely sum the total tax amount whether it is a V3 dict
            total_tc = 0.0
            for val in tax_values.values():
                if isinstance(val, dict):
                    total_tc += val.get('amount', 0.0)
                else:
                    total_tc += val
            self.total_tax_amount_tc = total_tc

        # warning: this is tightly coupled to TaxCloudRequest's _process_lines method
        # do not modify without syncing the other method
        for index, line in enumerate(
            self.order_line.filtered(
                lambda l: not (
                    l.display_type in ("line_note", "line_section")
                    or l.is_downpayment
                    or l.price_unit < 0
                )
            )
        ):
            if line._get_taxcloud_price() >= 0.0 and line.product_uom_qty >= 0.0:
                if not line.price_subtotal and self.env.company.is_skip_zero_orders:
                    continue
                price = (
                    line.price_unit
                    * (1 - (line.discount or 0.0) / 100.0)
                    * line.product_uom_qty
                )

                if not price:
                    final_rate = 0.0
                else:
                    # 1. Handle V3 vs V1
                    tax_data = tax_values.get(index, 0.0)
                    if isinstance(tax_data, dict):
                        tax_amount = tax_data.get('amount', 0.0)
                        api_rate = tax_data.get('rate')
                    else:
                        tax_amount = tax_data
                        api_rate = None

                    # 2. Strict API Rate Logic
                    if api_rate is not None:
                        # V3: Use exact API rate. No reverse engineering.
                        final_rate = api_rate * 100.0
                        tax_name = "%.4f %%" % float_round(final_rate, precision_digits=4)
                    else:
                        # V1 Legacy: Reverse compute
                        final_rate = (tax_amount / price) * 100.0
                        tax_name = "Tax %.4f %%" % float_round(final_rate, precision_digits=4)

                final_rate = float_round(final_rate, precision_digits=4)

                # 3. Clean Search
                tax = (
                    self.env["account.tax"]
                    .sudo().search(
                        [
                            *self.env["account.tax"]._check_company_domain(company),
                            ("name", "=", tax_name),
                            ("amount_type", "=", "percent"),
                            ("type_tax_use", "=", "sale"),
                        ],
                        limit=1,
                    )
                )

                if not tax:
                    if company.is_default_tax_template:
                        values = company.tax_template_id.copy_data()[0]
                        values.update({
                            "name": tax_name,
                            "amount": final_rate,
                            "description": tax_name,
                            "active": True,
                            "invoice_label": tax_name,
                        })
                    else:
                        values = {
                            "name": tax_name,
                            "amount": final_rate,
                            "amount_type": "percent",
                            "type_tax_use": "sale",
                            "description": tax_name,
                            "invoice_label": tax_name,
                        }
                    tax = (
                        self.env["account.tax"]
                        .sudo()
                        .with_context(default_company_id=company.id)
                        .create(values)
                    )
                line.tax_ids = tax
        return True

    def add_option_to_order_with_taxcloud(self):
        self.ensure_one()
        # portal user call this method with sudo
        if self.fiscal_position_id.is_taxcloud and self._uid == SUPERUSER_ID:
            self.validate_taxes_on_sales_order()

    def _action_confirm(self):
        res = super()._action_confirm()
        for order in self:
            order.validate_taxes_on_sales_order()
        return res

    def write(self, vals):
        res = super().write(vals)
        for order in self:
            if (
                order.is_taxcloud
                and order.state == "sale"
                and "partner_shipping_id" in vals
            ):
                raise UserError(
                    "You can't change the delivery address once \
the sale order is confirmed when using TaxCloud."
                    "\nIf you still need to change the delivery address, \
please reset the order to a quotation and then update the delivery address."
                )
        return res

    @api.onchange("partner_shipping_id")
    def _onchange_warning_partner_shipping_id(self):
        res = {}
        taxcloud_warning = (
            self.is_taxcloud
            and self.state == "sale"
            and self.partner_shipping_id
            and (self._origin.partner_shipping_id.id != self.partner_shipping_id.id)
        )
        if taxcloud_warning:
            res["warning"] = {
                "title": self.env._("TaxCloud Warning!"),
                "message": self.env._(
                    "You can't change the delivery address once \
the sale order is confirmed when using TaxCloud."
                    "\nIf you still need to change the delivery address,\
please reset the order to a quotation and then update the delivery address.",
                ),
            }
        return res

    def _check_taxcloud_response(self, response):
        if response.get('error_message'):
            raise ValidationError(
                self.env._("TaxCloud Connection Error:\n%s") % response['error_message']
            )

        if response.get('errors') or response.get('status', 200) >= 400:
            error_msg = ""
            for err in response.get('errors', []):
                location = err.get('location', 'Unknown')
                message = err.get('message', '')
                error_msg += f"{location}: {message}\n"

            if not error_msg:
                error_msg = response.get('detail') or response.get('title') or "Unknown Error"

            raise ValidationError(
                self.env._("Unable to process TaxCloud transaction:\n%s") % error_msg
            )

        return True

    def _prepare_down_payment_line_values_from_base_line(self, base_line):
        vals = super()._prepare_down_payment_line_values_from_base_line(base_line)
        zero_tax = self.env['account.tax'].search([
            ('amount', '=', 0),
            ('type_tax_use', '=', 'sale'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)

        if zero_tax:
            vals['tax_ids'] = [Command.set(zero_tax.ids)]
        else:
            vals['tax_ids'] = [Command.clear()]

        return vals


class SaleOrderLine(models.Model):
    """Defines getters to have a common facade for order and invoice lines in TaxCloud."""

    _inherit = "sale.order.line"

    def _get_taxcloud_price(self):
        self.ensure_one()
        return self.price_unit

    def _get_qty(self):
        self.ensure_one()
        return self.product_uom_qty

    @api.model_create_multi
    def create(self, vals_list):
        res = super().create(vals_list)
        for order in res.mapped("order_id").filtered(
            lambda x: x.state == "sale" and x.is_taxcloud
        ):
            order.validate_taxes_on_sales_order()
        return res

    def write(self, values):
        res = super().write(values)
        for record in self.filtered(
            lambda line: line.order_id.state == "sale" and line.order_id.is_taxcloud
        ):
            if (
                "product_uom_qty" in values
                or "price_unit" in values
                or ("discount" in values and values.get("discount") != record.discount)
            ):
                record.order_id.validate_taxes_on_sales_order()
        return res
