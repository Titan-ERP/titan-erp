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

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from ast import literal_eval

from .taxcloud_request import TaxCloudRequest

_logger = logging.getLogger(__name__)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    module_account_taxcloud_tc = fields.Boolean(string="Account TaxCloud TC")
    taxcloud_api_version = fields.Selection(
        related='company_id.taxcloud_api_version',
        readonly=False,
        string="TaxCloud API Version"
    )
    taxcloud_api_id_v3 = fields.Char(related="company_id.taxcloud_api_id_v3", readonly=False)
    taxcloud_api_key_v3 = fields.Char(related="company_id.taxcloud_api_key_v3", readonly=False)
    tic_category_id = fields.Many2one(
        related="company_id.tic_category_id", string="Default TIC Code", readonly=False
    )
    is_default_tax_template = fields.Boolean(
        related="company_id.is_default_tax_template",
        string="Default Tax Template",
        readonly=False,
    )
    tax_template_id = fields.Many2one(
        "account.tax",
        related="company_id.tax_template_id",
        string="Tax Template",
        domain=[("type_tax_use", "=", "sale")],
        readonly=False,
    )
    notify_email_sent = fields.Boolean(
        string="Notify Email Sent",
        config_parameter="account_taxcloud_tc.notify_email_sent",
        readonly=True,
    )
    is_skip_zero_invoice = fields.Boolean(
        string="Skip Zero Invoice",
        related="company_id.is_skip_zero_invoice",
        readonly=False,
    )
    is_allow_cancel_invoice = fields.Boolean(
        string="Allow Cancel Invoice",
        related="company_id.is_allow_cancel_invoice",
        readonly=False,
    )
    taxcloud_api_call_count = fields.Integer(
        string="API Call Count",
        readonly=True,
        config_parameter='account_taxcloud_tc.taxcloud_api_call_count'
    )
    taxcloud_api_threshold_limit = fields.Integer(
        string="API Threshold Limit",
        config_parameter='account_taxcloud_tc.taxcloud_api_threshold_limit',
        default=100,
    )
    taxcloud_api_call_notification_ids = fields.Many2many(
        'res.partner',
        string="API Usage Alert Email Recipients",
    )

    @api.model
    def get_values(self):
        res = super().get_values()
        ICPSudo = self.env["ir.config_parameter"].sudo()
        calls = ICPSudo.get_param("account_taxcloud_tc.taxcloud_api_call_notification_ids")
        res.update(
            taxcloud_api_call_notification_ids=[(6, 0, literal_eval(calls))]
            if calls and calls not in ('[]', '') else False,
        )
        return res

    def set_values(self):
        res = super().set_values()
        ICPSudo = self.env["ir.config_parameter"].sudo()
        ICPSudo.set_param(
            "account_taxcloud_tc.taxcloud_api_call_notification_ids",
            self.taxcloud_api_call_notification_ids.ids
        )
        return res

    @api.onchange("is_default_tax_template")
    def onchange_is_default_tax_template(self):
        if not self.is_default_tax_template and self.tax_template_id:
            self.tax_template_id = False

    def sync_taxcloud_category(self):
        """
        When the user clicks 'Sync' in settings, trigger the company-level
        sync for the currently active company.
        """
        if not self.env.company.is_taxcloud_configured:
            raise ValidationError(self.env._("Please configure your TaxCloud API keys first."))

        self.env.company._cron_sync_taxcloud_tic_categories()

        return True

    def action_send_notify(self):
        self.ensure_one()
        template = self.env.ref(
            "account_taxcloud_tc.email_template_taxcloud_notify",
            raise_if_not_found=False,
        )
        compose_form = self.env.ref("mail.email_compose_message_wizard_form")
        ctx = dict(
            default_model="res.partner",
            default_res_ids=self.env.user.partner_id.ids,
            default_template_id=template.id if template else False,
            default_composition_mode="comment",
            default_email_layout_xmlid="mail.mail_notification_light",
        )
        return {
            "name": _("Compose Email"),
            "type": "ir.actions.act_window",
            "view_mode": "form",
            "res_model": "mail.compose.message",
            "views": [(compose_form.id, "form")],
            "view_id": compose_form.id,
            "target": "new",
            "context": ctx,
        }
