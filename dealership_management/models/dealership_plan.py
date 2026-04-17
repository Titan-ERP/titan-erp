# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
import logging
from odoo import api, fields, models

_log = logging.getLogger(__name__)


class Plan(models.Model):
	_name        = 'dealership.plan'
	_description = 'Plans for the dealership application'
	_rec_name    = 'name'

	name  = fields.Char(required=True, related="product_id.name", readonly=False, store=True, translate=False)
	product_id = fields.Many2one('product.product', string='Product')
	price = fields.Float(required=True, related="product_id.lst_price", readonly=False)
	type  = fields.Selection(selection=[('sale', 'Sale'),('service', 'Service'),], string='Plan Type', required=True)
	duration = fields.Integer('Months', required=True)
	terms = fields.Text('Terms and conditions')
	image = fields.Image("Image", related="product_id.image_1920", readonly=False)
	currency_id = fields.Many2one('res.currency', 'Currency', compute='_compute_currency_id')


	def _compute_currency_id(self):
		main_company = self.env['res.company']._get_main_company()
		for template in self:
			template.currency_id = main_company.currency_id.id

	
	@api.model_create_multi
	def create(self, vals):
		for vals in vals:
			param = {
				'name': vals.get('name'),
				'type': 'service',
				'lst_price': vals.get('price'),
				'default_code': "DLP_{}".format(vals.get('name')),
				'taxes_id': [(6, 0, [])],
				'website_published': True,
				'purchase_ok': False,
				'sale_ok': False,
			}

			product = self.env['product.product'].create(param)
			vals.update({'product_id': product.id})
		return super(Plan, self).create(vals)


	@api.model
	def generate_contracts(self, application_id):
		self.ensure_one()
		if application_id:
			vals = {
				'application_id': application_id,
				'date_from': fields.Date.today(),
				'state': 'draft',
			}
			contract = self.env['dealership.contract'].sudo().create(vals)
			contract.application_id.plan_id = self.id
			return contract

	def get_price(self, price_list_id=0):
		self.ensure_one()
		price = self.price
		price_list = self.env['product.pricelist'].browse(price_list_id)

		if price_list.exists():
			company_id =  self.env['res.company'].browse(self.env.context.get('company_id')) or self.env.company
			price = self.product_id.currency_id._convert(price, price_list.currency_id, company_id, fields.Date.today())
		return price
