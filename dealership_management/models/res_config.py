# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################

from odoo import fields, models, api

class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'


    hide_advertisement_tab = fields.Boolean(string="Advertisement Details", related="website_id.hide_advertisement_tab", readonly=False)
    hide_site_location_tab = fields.Boolean(string="Site Location", related="website_id.hide_site_location_tab", readonly=False)
    hide_investment_tab = fields.Boolean(string="Investment Capacity", related="website_id.hide_investment_tab", readonly=False)
    sign_up_banner = fields.Image('Sign Up Banner', related="website_id.sign_up_banner", readonly=False)
    allow_user_signup = fields.Boolean('Allow Register', related="website_id.allow_user_signup", readonly=False)
    signup_closed_description = fields.Text('Description On Website', related="website_id.signup_closed_description", readonly=False)
    allow_dealer_locator = fields.Boolean(string="Allow Dealer Location", related="website_id.allow_dealer_locator", readonly=False)
    before_notify = fields.Integer(string='Notification Message', help="Notify user before days to expire the contract", default=1)

    google_map_api_key = fields.Char(
        string='Google Map API Key',
        readonly=False,
        help="Visit https://developers.google.com/maps/documentation/geocoding/get-api-key for more information.",
    )

    allow_dealer_application = fields.Selection(string="Allow Applications", help="Allow applications to set as deaer at time",
        readonly=False,
		selection = [
			('creation_time', 'Creation Time'),
			('manually', 'Manually')
	])

    @api.onchange('google_map_api_key')
    def change_google_map_api_key(self):
        if hasattr(self, 'geoloc_provider_googlemap_key'):
            self.geoloc_provider_googlemap_key = self.google_map_api_key


    def set_values(self):
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        base_geolocalize = self.env['ir.module.module'].search([('name', '=', 'base_geolocalize')])

        if base_geolocalize and base_geolocalize.state == 'installed':
            self.google_map_api_key = self.geoloc_provider_googlemap_key

        IrConfigParameter.set_param('dealership_management.google_map_api_key', self.google_map_api_key)
        IrConfigParameter.set_param('dealership_management.before_notify', self.before_notify)
        IrConfigParameter.set_param('dealership_management.allow_dealer_application', self.allow_dealer_application)

        super(ResConfigSettings, self).set_values()


    @api.model
    def get_values(self):
        res = super(ResConfigSettings, self).get_values()
        IrConfigParameter = self.env['ir.config_parameter'].sudo()
        res.update({
            'google_map_api_key': IrConfigParameter.get_param('dealership_management.google_map_api_key') ,
            'before_notify': int(IrConfigParameter.get_param('dealership_management.before_notify') or 1),
            'allow_dealer_application': IrConfigParameter.get_param('dealership_management.allow_dealer_application')
        })
        return res


    def open_template_dealer(self):
        action = self.env.ref('dealership_management.dealership_application_action').read()[0]
        action['res_id'] = self.env.ref('dealership_management.template_dealer_id').id
        action['views'] = [[self.env.ref('dealership_management.dealership_application_form').id, 'form']]
        return action
