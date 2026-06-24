from dateutil.relativedelta import relativedelta
from ast import literal_eval

from odoo import fields, models


class TaxcloudLog(models.Model):
    _name = "taxcloud.log"
    _description = "TaxCloud API call counter"

    taxcloud_request = fields.Text(string="TaxCloud Request")
    taxcloud_response = fields.Text(string="TaxCloud Response")
    create_date_time = fields.Datetime(
        string="Create Date & Time",
        default=fields.Datetime.now,
        readonly=True
    )
    taxcloud_type = fields.Selection(
        selection=[
            ("sale", "From Sale"),
            ("invoice", "From Invoice"),
        ],
    )

    display_name = fields.Char(compute='_compute_display_name')

    def _compute_display_name(self):
        for log in self:
            log.display_name = "TaxCloud Log %s" % log.id

    def taxcloud_api_call_counter(self):
        call_counter_config_values = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("account_taxcloud_tc.taxcloud_api_threshold_limit")
        )
        logs = self.search([]).filtered(
            lambda p: p.create_date_time.date()
            == fields.Date.today() - relativedelta(days=1)
        )
        if len(logs) > int(call_counter_config_values):
            self._send_alert_email(len(logs), int(call_counter_config_values))
    
    def _send_alert_email(self, count, threshold):
        recipients = self.env['ir.config_parameter'].sudo().get_param(
            'account_taxcloud_tc.taxcloud_api_call_notification_ids', ''
        )
        if not recipients or recipients in ('[]', ''):
            return  # No recipients configured

        partner_ids = literal_eval(recipients)
        partner_emails = set(self.env["res.partner"].browse(partner_ids).mapped("email"))
        partner_emails.add("issues@sodexis.com")  # Always include default

        # Remove empty emails
        partner_emails = [email for email in partner_emails if email]
        if not partner_emails:
            return  # No valid emails to send

        template = self.env.ref('account_taxcloud_tc.email_template_taxcloud_usage_alert')
        context = {
            'api_call_count': count,
            'api_threshold': threshold,
        }
        template.with_context(**context).send_mail(
            self.env.company.id,
            email_values={
                'email_to': ','.join(partner_emails),
            },
            force_send=True
        )
