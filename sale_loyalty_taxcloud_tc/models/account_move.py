from odoo import api, models

from .taxcloud_request import TaxCloudRequest


class AccountMove(models.Model):
    _inherit = "account.move"

    @api.model
    def _get_TaxCloudRequest(self, api_id, api_key, api_version='v3'):
        return TaxCloudRequest(self.env, api_id, api_key, api_version)
