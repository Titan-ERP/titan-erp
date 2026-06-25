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

import re

from odoo import api, models


class AccountTax(models.Model):
    _inherit = "account.tax"

    @api.onchange("name")
    def onchange_name(self):
        name = self.name
        amount_str = "%.3f " % (self.amount)
        if (
            name
            and amount_str not in name
            and self.amount
            and self.amount_type in ("percent", "division")
        ):
            self.name = re.sub(r"\s+\d*\.?\d*\s?%", " %.3f %%" % (self.amount), name)

    @api.onchange("amount")
    def onchange_amount(self):
        name = self.name
        if name and self.amount_type in ("percent", "division"):
            self.name = re.sub(r"\s+\d*\.?\d*\s?%", " %.3f %%" % (self.amount), name)

    def _is_taxcloud_from_base_lines(self, base_lines):
        for base_line in base_lines:
            record = base_line.get("record")
            if not record or isinstance(record, dict):
                continue

            if record._name == "sale.order.line":
                if record.order_id.fiscal_position_id.is_taxcloud:
                    return True

            elif record._name == "account.move.line":
                if record.move_id.fiscal_position_id.is_taxcloud:
                    return True

        return False

    def _reduce_base_lines_to_target_amount(
        self,
        base_lines,
        company,
        amount_type,
        amount,
        computation_key=None,
        grouping_function=None,
        aggregate_function=None,
    ):
        """
        Override to apply TaxCloud proportional distribution on price_unit.
        """

        # Apply ONLY for TaxCloud
        if not self._is_taxcloud_from_base_lines(base_lines):
            return super()._reduce_base_lines_to_target_amount(
                base_lines,
                company,
                amount_type,
                amount,
                computation_key=computation_key,
                grouping_function=grouping_function,
                aggregate_function=aggregate_function,
            )

        if not base_lines:
            return []

        currency = base_lines[0]["currency_id"]

        def grouping_function_total(base_line, tax_data):
            return True

        base_lines_aggregated_values = self._aggregate_base_lines_tax_details(
            base_lines, grouping_function_total
        )
        values_per_grouping_key = self._aggregate_base_lines_aggregated_values(
            base_lines_aggregated_values
        )

        total_amount_currency = sum(
            values["total_excluded_currency"] + values["tax_amount_currency"]
            for values in values_per_grouping_key.values()
        )

        sign = -1 if amount < 0.0 else 1
        signed_amount = sign * amount

        if amount_type == "fixed":
            percentage = (
                (signed_amount / total_amount_currency)
                if total_amount_currency
                else 0.0
            )
            expected_total_amount_currency = currency.round(amount)
        else:
            percentage = signed_amount / 100.0
            expected_total_amount_currency = currency.round(
                total_amount_currency * sign * percentage
            )

        reduced_base_lines = self._reduce_base_lines_with_grouping_function(
            base_lines=base_lines,
            grouping_function=grouping_function,
            aggregate_function=aggregate_function,
            computation_key=computation_key,
        )

        if not reduced_base_lines:
            return []

        total_base_amount = sum(bl["price_unit"] for bl in reduced_base_lines)

        new_base_lines = []
        for base_line in reduced_base_lines:
            if not total_base_amount:
                new_price_unit = 0.0
            else:
                ratio = base_line["price_unit"] / total_base_amount
                allocated = expected_total_amount_currency * ratio
                new_price_unit = currency.round(allocated)

            new_base_lines.append(
                self._prepare_base_line_for_taxes_computation(
                    base_line,
                    price_unit=new_price_unit,
                    computation_key=computation_key,
                )
            )
        self._add_tax_details_in_base_lines(new_base_lines, company)
        self._round_base_lines_tax_details(new_base_lines, company)

        diff = expected_total_amount_currency - sum(
            bl["price_unit"] for bl in new_base_lines
        )
        if new_base_lines:
            new_base_lines[0]["price_unit"] += diff

        return new_base_lines
