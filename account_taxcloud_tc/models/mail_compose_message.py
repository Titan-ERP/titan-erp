from odoo import models


class MailComposeMessage(models.TransientModel):
    _inherit = "mail.compose.message"

    def _action_send_mail(self, auto_commit=False):
        result_mails_su, result_messages = super()._action_send_mail(
            auto_commit=auto_commit
        )
        notify_email_sent = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param("account_taxcloud_tc.notify_email_sent")
        )
        if (
            not notify_email_sent
            and self.template_id.id
            == self.env.ref("account_taxcloud_tc.email_template_taxcloud_notify").id
        ):
            self.env["ir.config_parameter"].sudo().set_param(
                "account_taxcloud_tc.notify_email_sent", True
            )
        return result_mails_su, result_messages
