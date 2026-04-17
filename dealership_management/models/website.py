# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
import logging
from odoo import api, fields, models, _

_log = logging.getLogger(__name__)

class SaleOrder(models.Model):
	_inherit = 'sale.order'

	dealer_payment_status = fields.Boolean(default=False, string="Line Assigned To Application")

	def _action_confirm(self):
		response = super(SaleOrder, self)._action_confirm()
		for sale_order in self:
			"""used to check order line contains application id"""
			try:
				order_line = sale_order.order_line.filtered(lambda x: x.is_dealer_application and x.application_id)
				order_lines = sale_order.order_line.filtered(lambda x: x.is_dealer_application and not x.application_id)
				user_id = self.env['res.users'].sudo().search([('partner_id', '=', sale_order.partner_id.id)], limit=1)

				if order_line:
					order_line = order_line[0]
					application_id = order_line.application_id
					plan_id = self.env['dealership.plan'].sudo().search([('product_id', '=', order_line.product_id.id)], limit=1)
					if plan_id:
						if not user_id:
							application_id.create_user(self.env.context.get('lang', False))
						application_id.plan_id = plan_id.id
						contract_id = application_id.get_related_contract()
						contract_id.create_contract_line(sale_order.id)
						application_id.state = 'done'
						mail_template = self.env.ref('dealership_management.dealership_appication_done_status').sudo()
						mail_template.send_mail(application_id.id,force_send=True)
				elif order_lines and user_id:
					application_id = user_id.application_id
					for line in order_lines:
						already_added = application_id.get_product_stock(line.product_id.id)
						if already_added:
							already_added.qty = already_added.qty + line.product_uom_qty
						else:
							already_added = self.env['dealer.stock.line'].create({
								'product_id': line.product_id.id,
								'qty': line.product_uom_qty,
								'application_id': application_id.id,
							})
						already_added.order_ids |= line.order_id
			except Exception as e:
				_log.info("Dealership Error: %r", e)
		return response


class SaleOrderLine(models.Model):
	_inherit = 'sale.order.line'

	is_dealer_application = fields.Boolean(default=False)
	application_id = fields.Many2one('dealership.application')

	def _is_sellable(self):
		return super()._is_sellable() and (
			not self.application_id
		)

class Website(models.Model):
	_inherit = 'website'

	@api.model
	def get_countries(self):
		countries = self.env['res.country'].sudo().search([])
		return countries

	@api.model
	def get_default_country(self):
		country = self.env['res.country'].sudo().search([('code','=','IN')])
		return country

	@api.model
	def get_country_states(self):
		states = self.env['res.country.state'].sudo().search([])
		return states

	@api.model
	def get_business_type(self):
		business = self.env['business.type'].sudo().search([])
		return business

	@api.model
	def get_application_change_state_date(self, application, state):
		if state == 'submitted':
			return application.create_date.date()
		elif state == 'pending':
			return application.date_pending
		elif state == 'processed':
			return application.date_processed
		elif state == 'approved':
			return application.date_approved
		elif state == 'decline':
			return application.date_declined
		elif state == 'done':
			return application.date_done
		return False


	'''config setting for dealer application'''
	hide_advertisement_tab = fields.Boolean(string="Advertisement Details")
	hide_site_location_tab = fields.Boolean(string="Site Location")
	hide_investment_tab = fields.Boolean(string="Investment Capacity")
	sign_up_banner = fields.Image('Sign Up Banner')
	allow_user_signup = fields.Boolean('Allow Register', default=True)
	signup_closed_description = fields.Text('Description On Website', default="New Dealers Not Allowed To Register")
	allow_dealer_locator = fields.Boolean(string="Allow Dealer Location", default=True)
