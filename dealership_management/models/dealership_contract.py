# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################

import logging

from odoo import fields, models, api, _
from odoo.tools import date_utils

_log = logging.getLogger(__name__)

class Contract(models.Model):
	_inherit = ['portal.mixin']
	_name = 'dealership.contract'
	_description = 'Contract'
	_rec_name = 'partner_id'


	application_id  = fields.Many2one('dealership.application',required=True)
	date_terminated = fields.Date('Terminated Date')
	date_from       = fields.Date('Start Date')
	date_to         = fields.Date('End Date')

	state = fields.Selection(
		selection=[
			('draft','Draft'),
			('ongoing','Ongoing'),
			('expired','Expired'),
			('terminated','Terminated'),
		],
		string='Status',
		default='draft',
	)

	partner_id = fields.Many2one(
		related='application_id.partner_id',
		string='Dealer',
		required=True
	)

	plan_id = fields.Many2one(
		related='application_id.plan_id',
		string='Plan',
		required=True
	)

	duration = fields.Integer(related='plan_id.duration')
	price    = fields.Float(related='plan_id.price')
	terms    = fields.Text(related='plan_id.terms')
	contract_line = fields.One2many('dealership.contract.line', 'contract_id', string='Contract Lines', copy=True)


	def _compute_access_url(self):
		for record in self:
			record.access_url = '/dealer/get_contract_pdf/%s' % record.id

	def get_active_contract_line(self):
		self.ensure_one()
		contract_line = self.contract_line.filtered(lambda x: x.state=='ongoing')
		if contract_line:
			return contract_line[0]
		return False

	def create_contract_line(self, order_id):
		vals = { 'sale_order_id': order_id }
		remaining_days = 0

		if self.application_id.state != 'done':
			self.date_from = fields.Date.today()
		else:
			active_line = self.get_active_contract_line()

			if active_line:
				active_line.state = 'expired'
				if active_line._is_valid_contract():
					remaining_days = active_line.total_days - active_line.days_to_left

		date_to = fields.Date.today() + date_utils.relativedelta(months=self.duration, days=remaining_days)
		self.date_to = date_to
		vals['date_to'] = date_to
		self.sudo().contract_line = [(0, 0, vals)]
		self.state = 'ongoing'


	def notify_users(self):
		contracts = self.search([('contract_line.state','=', 'ongoing')])
		before_expire = self.env['ir.config_parameter'].sudo().get_param('dealership_management.before_notify') or 1
		for record in contracts:
			try:
				active_line = record.get_active_contract_line()
				remaining_days = active_line.total_days - active_line.days_to_left
				if remaining_days <= int(before_expire):
					mail_template = self.env.ref('dealership_management.dealership_appication_expire_status')
					mail_template.send_mail(record.id, force_send=True)
				if not remaining_days:
					record.state = 'expired'
					active_line.state = 'expired'
			except Exception as e:
				pass
		return True


	def is_need_to_update(self):
		before_expire = self.env['ir.config_parameter'].sudo().get_param('dealership_management.before_notify') or 1
		active_line = self.get_active_contract_line()
		remaining_days = active_line.total_days - active_line.days_to_left
		if remaining_days <= int(before_expire):
			return True
		return False


class ContractLine(models.Model):
	_name = 'dealership.contract.line'
	_description = 'Contract Line'
	_rec_name = 'sale_order_id'
	_order = "date_to desc"


	@api.depends('date_from', 'date_to')
	def _compute_total_days(self):
		for record in self:
			d1 = record.date_from
			d2 = record.date_to
			delta = d2 - d1
			record.total_days = delta.days

	def _compute_days_to_left(self):
		for record in self:
			d1 = record.date_from
			d2 = fields.Date.today()
			delta = d2 - d1
			record.days_to_left = delta.days

	def _is_valid_contract(self):
		self.ensure_one()
		return self.total_days >= self.days_to_left


	contract_id = fields.Many2one('dealership.contract', string='Contract Reference', required=True, ondelete='cascade', index=True, copy=False)
	sale_order_id = fields.Many2one('sale.order', 'Order Payment', required=True)
	date_from = fields.Date('Start Date', default=fields.Date.today(), required=True)
	date_to = fields.Date('End Date', required=True)
	total_days = fields.Integer('Total Days', compute='_compute_total_days', store=True)
	days_to_left = fields.Integer('Expiry Days', compute='_compute_days_to_left')

	state = fields.Selection(
		selection=[
			('ongoing','Ongoing'),
			('expired','Expired')
		],
		string='Status',
		default='ongoing'
	)
