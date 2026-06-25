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

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_compare, float_round

from .taxcloud_request import TaxCloudRequest

_logger = logging.getLogger(__name__)


class AccountMove(models.Model):
    _inherit = "account.move"

    # Used to determine whether or not to warn the user to configure TaxCloud
    is_taxcloud_configured = fields.Boolean(related="company_id.is_taxcloud_configured")
    # Technical field to determine whether to hide taxes in views or not
    is_taxcloud = fields.Boolean(related="fiscal_position_id.is_taxcloud")
    total_tax_amount_tc = fields.Float("TaxCloud Total Tax")
    taxcloud_orderid = fields.Char(
        "TaxCloud Order ID",
        help="The TaxCloud Order ID is used to identify the order in TaxCloud.",
        readonly=True,
        copy=False,
    )
    is_allow_cancel_invoice = fields.Boolean(
        related="company_id.is_allow_cancel_invoice", compute_sudo=True
    )
    taxcloud_cart_id = fields.Char("TaxCloud Cart ID", copy=False, help="V3 Cart UUID")

    def _post(self, soft=True):
        # OVERRIDE

        # Don't change anything on moves used to cancel another ones.
        if self.env.context.get("move_reverse_cancel"):
            return super()._post(soft)

        refund_with_out_reverse = self.filtered(
            lambda move: move.fiscal_position_id.is_taxcloud
            and move.move_type == "out_refund"
            and not move.reversed_entry_id
        )
        if refund_with_out_reverse:
            raise UserError(
                _(
                    "This credit note cannot be posted because it isn't linked to an "
                    "original invoice and the fiscal position uses TaxCloud.\n"
                    "Please cancel it and recreate the credit note from the original invoice."
                )
            )

        invoices_to_validate = self.filtered(
            lambda move: move.is_sale_document()
            and move.fiscal_position_id.is_taxcloud
            and ((not move._is_downpayment()) or (move._is_downpayment() and len(move.invoice_line_ids) > 1))
        )

        if invoices_to_validate:
            for invoice in invoices_to_validate.with_context(
                taxcloud_authorize_transaction=True
            ):
                # Use the invoice for document type checks and company-level fields
                is_deferred_expense = invoice.is_purchase_document()

                # Safely get deferred account only if fields exist on the invoice company
                deferred_account = False
                company = invoice.company_id
                if hasattr(company, "deferred_expense_account_id") and hasattr(company, "deferred_revenue_account_id"):
                    deferred_account = (
                        company.deferred_expense_account_id if is_deferred_expense else company.deferred_revenue_account_id
                    )

                # Safely check subscription on invoice lines and deprecated field on deferred account
                has_subscription = hasattr(invoice.invoice_line_ids, 'subscription_id') and invoice.invoice_line_ids.subscription_id
                is_deferred_deprecated = bool(getattr(deferred_account, "deprecated", False))

                if not (has_subscription and is_deferred_deprecated):
                    invoice.validate_taxes_on_invoice()
        return super()._post(soft)

    def button_draft(self):
        """At confirmation below, the AuthorizedWithCapture encodes the invoice
        in TaxCloud. Returned cancels it for a refund.
        See https://dev.taxcloud.com/taxcloud/guides/5%20Returned%20Orders
        """
        if self.filtered(
            lambda inv: inv.move_type in ["out_invoice", "out_refund"]
            and inv.fiscal_position_id.is_taxcloud
            and inv.payment_state in ["paid", "in_payment"]
        ):
            raise UserError(
                self.env._(
                    "You cannot cancel an invoice sent to TaxCloud.\n"
                    "You need to issue a refund (credit note) for it instead.\n"
                    "This way the tax entries will be cancelled in TaxCloud."
                )
            )
        return super().button_draft()

    @api.model
    def _get_TaxCloudRequest(self, api_id, api_key, api_version='v3'):
        return TaxCloudRequest(self.env, api_id, api_key, api_version=api_version)

    def get_taxcloud_reporting_date(self):
        if self.invoice_date:
            return datetime.datetime.combine(
                self.invoice_date, datetime.datetime.min.time()
            )
        else:
            return fields.Datetime.context_timestamp(self, datetime.datetime.now())

    # Used to prepare the taxcloud request
    # So that we can inherit this method in another modules to update the request.
    def prepare_taxcloud_request(self):
        shipper = self.company_id or self.env.company
        api_version = shipper.taxcloud_api_version

        if api_version == 'v3':
            api_id = (shipper.taxcloud_api_id_v3 or "").strip()
            api_key = (shipper.taxcloud_api_key_v3 or "").strip()

        request = self._get_TaxCloudRequest(api_id, api_key, api_version)
        request.set_location_origin_detail(shipper)
        request.set_location_destination_detail(self.partner_shipping_id)
        request.set_invoice_items_detail(self)
        return request

    # flake8: noqa: C901
    def validate_taxes_on_invoice(self):
        self.ensure_one()
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
                self.invoice_line_ids.filtered(
                    lambda x: x.display_type not in ("line_note", "line_section")
                )
            )
            and self.env.company.is_skip_zero_invoice
        ):
            return True

        if (
            float_compare(
                self.amount_total, 0.0, precision_rounding=self.currency_id.rounding
            )
            < 0
        ):
            raise UserError(
                self.env._(
                    "You cannot validate a TaxCloud invoice with a negative total amount. "
                    "You should create a credit note instead. "
                    "Use the action menu to transform it into a credit note or refund."
                )
            )

        response = request.get_all_taxes_values()
        cart_id = response.get("cart_id", {})

        if cart_id:
            self.taxcloud_cart_id = cart_id

        self._check_taxcloud_response(response)

        tax_values = response.get("values", {})

        # Calculate total tax safely regardless of V1 (float) or V3 (dict) format
        total_tc = 0.0
        for val in tax_values.values():
            if isinstance(val, dict):
                total_tc += val.get('amount', 0.0)
            else:
                total_tc += val
        self.total_tax_amount_tc = total_tc

        # warning: this is tightly coupled to TaxCloudRequest's _process_lines method
        # do not modify without syncing the other method
        raise_warning = False
        tax_value_index = 0

        invoice_lines = self.invoice_line_ids.filtered(
            lambda l: not (
                l.display_type in ("line_note", "line_section")
                or l.is_downpayment
                or l.price_unit < 0
            )
        )

        for line in invoice_lines:
            line.tax_ids = False

            if line._get_taxcloud_price() >= 0.0 and line.quantity >= 0.0:

                if not line.price_subtotal and self.env.company.is_skip_zero_invoice:
                    tax_value_index += 1
                    continue

                price = (
                    line.price_unit
                    * (1 - (line.discount or 0.0) / 100.0)
                    * line.quantity
                )

                if not price:
                    final_rate = 0.0
                else:
                    tax_data = tax_values.get(tax_value_index, 0.0)
                    if isinstance(tax_data, dict):
                        tax_amount = tax_data.get('amount', 0.0)
                        api_rate = tax_data.get('rate')
                    else:
                        tax_amount = tax_data
                        api_rate = None

                    if api_rate is not None:
                        final_rate = api_rate * 100.0
                        tax_name = "%.4f %%" % float_round(final_rate, precision_digits=4)
                    else:
                        final_rate = (tax_amount / price) * 100.0
                        tax_name = "Tax %.4f %%" % float_round(final_rate, precision_digits=4)

                final_rate = float_round(final_rate, precision_digits=4)
                tax_value_index += 1

                tax = self.env["account.tax"].sudo().search(
                    [
                        *self.env["account.tax"]._check_company_domain(company),
                        ("name", "=", tax_name),
                        ("amount_type", "=", "percent"),
                        ("type_tax_use", "=", "sale"),
                    ],
                    limit=1,
                )

                if not tax:
                    tax_group = self.env['account.tax.group'].sudo().search([
                        ('name', '=', 'TaxCloud Sales Tax'),
                        ('company_id', '=', company.id)
                    ], limit=1)

                    if not tax_group:
                        tax_group = self.env['account.tax.group'].sudo().create({
                            'name': 'TaxCloud Sales Tax',
                            'company_id': company.id,
                        })

                    if company.is_default_tax_template:
                        values = company.tax_template_id.copy_data()[0]
                        values.update({
                            "name": tax_name,
                            "amount": final_rate,
                            "invoice_label": tax_name,
                            "description": tax_name,
                            "active": True,
                            "tax_group_id": tax_group.id,
                        })
                    else:
                        values = {
                            "name": tax_name,
                            "amount": final_rate,
                            "amount_type": "percent",
                            "type_tax_use": "sale",
                            "description": tax_name,
                            "tax_group_id": tax_group.id,
                            "invoice_label": tax_name
                        }

                    tax = (
                        self.env["account.tax"]
                        .sudo()
                        .with_context(default_company_id=company.id)
                        .create(values)
                    )

                # Assign created/found tax to the line (recordset assignment works in v19)
                line.tax_ids = tax

        if self.env.context.get("taxcloud_authorize_transaction"):
            reporting_date = self.get_taxcloud_reporting_date()

            if self.move_type == "out_invoice":
                order_id = self._get_taxcloud_orderid()
                response = request.get_taxcloud_authorize_with_capture(
                    self,
                    order_id,
                    reporting_date,
                )
                self._check_taxcloud_response(response)

            elif self.move_type == "out_refund":
                request.set_invoice_items_detail(self)
                origin_invoice = self.reversed_entry_id
                if origin_invoice:
                    origin_invoice = origin_invoice._get_taxcloud_orderid()
                    response = request.get_taxcloud_returned(
                        origin_invoice, self.invoice_date
                    )
                    if isinstance(response, dict):
                        self._check_taxcloud_response(response)
                else:
                    _logger.warning(
                        """The source document on the refund is not valid"""
                        """ and thus the refunded cart won't be logged on"""
                        """ your taxcloud account."""
                    )

        if raise_warning:
            return {
                "warning": self.env._(
                    """The tax rates have been updated, """
                    """ you may want to check it before validation"""
                )
            }
        else:
            return True

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

    def _get_taxcloud_orderid(self):
        """Return the TaxCloud Order ID, or fallback to the record ID if not set."""
        return self.taxcloud_orderid or str(self.id)

    def _cancel_in_taxcloud(self, request, invoice_id, invoice_date):
        """Helper to cancel an invoice in TaxCloud and handle errors."""
        response = request.get_taxcloud_returned(
            invoice_id, self.invoice_date
        )
        if isinstance(response, dict):
            self._check_taxcloud_response(response)
        return response

    def action_cancel_in_taxcloud(self):
        """Cancel invoice in TaxCloud and update the TaxCloud Order ID for uniqueness."""
        for record in self.filtered(
            lambda move: move.is_taxcloud and move.move_type == "out_invoice"
        ):
            request = record.prepare_taxcloud_request()
            invoice_id = record._get_taxcloud_orderid()
            record._cancel_in_taxcloud(request, invoice_id, record.invoice_date)
            record.button_cancel()
            if not record.taxcloud_orderid:
                record.taxcloud_orderid = f"{record.id}-1"
            else:
                parts = record.taxcloud_orderid.split("-")
                if len(parts) == 2 and parts[1].isdigit():
                    next_id = int(parts[1]) + 1
                else:
                    next_id = 1
                record.taxcloud_orderid = f"{parts[0]}-{next_id}"


class AccountMoveLine(models.Model):
    """Defines getters to have a common facade for order and move lines in TaxCloud."""

    _inherit = "account.move.line"

    def _get_taxcloud_price(self):
        self.ensure_one()
        return self.price_unit

    def _get_qty(self):
        self.ensure_one()
        return self.quantity
