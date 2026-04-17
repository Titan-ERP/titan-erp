# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
import logging
import math
from random import SystemRandom
from odoo import api, exceptions, fields, models, _

_log = logging.getLogger(__name__)

def random_token():
	chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789'
	return ''.join(SystemRandom().choice(chars) for _ in range(5))

NotificationMessage = {
	'type': 'simple_notification',
	'title': 'Dealership Application',
}

class Application(models.Model):
	_name = 'dealership.application'
	_inherit = ['mail.activity.mixin','rating.mixin']
	_description = 'Application'
	_rec_name = 'name'
	_order = 'create_date desc'

	# res.partner fields
	name       = fields.Char(required=True, related='partner_id.name', store=True, readonly=False)
	email      = fields.Char(required=True, related='partner_id.email', store=True, readonly=False)
	phone      = fields.Char(required=True, related='partner_id.phone', store=True, readonly=False)
	street     = fields.Char(required=True, related='partner_id.street', store=True, readonly=False)
	street2    = fields.Char(related='partner_id.street2', store=True, readonly=False)
	zip        = fields.Char(change_default=True, related='partner_id.zip', store=True, readonly=False)
	city       = fields.Char(required=True, related='partner_id.city', store=True, readonly=False)
	state_id   = fields.Many2one('res.country.state','State',ondelete='restrict', related='partner_id.state_id', store=True, readonly=False)
	country_id = fields.Many2one('res.country','Country',ondelete='restrict', required=True, related='partner_id.country_id', store=True, readonly=False)
	image      = fields.Image("Image", related="partner_id.image_1920", readonly=False)

	@api.depends('application_history_ids')
	def _get_history_count(self):
		for application in self:
			if application.application_history_ids:
				application.history_count = len(application.application_history_ids)
			else:
				application.history_count = 0


	def _get_product_count(self):
		for record in self:
			if record.stock_available:
				record.product_count = len(record.stock_available)
			else:
				record.product_count = 0


	def _get_order_count(self):
		for record in self:
			domain = [('message_partner_ids', 'child_of', [record.partner_id.commercial_partner_id.id])]
			search_count = self.env['sale.order'].search_count(domain)
			record.order_count = search_count

	def product_normal_action_sell(self):
		action = self.env.ref('dealership_management.dealer_stock_action').read()[0]
		action['domain'] = [('application_id', '=', self.id)]
		return action

	def action_orders(self):
		action = self.env.ref('sale.action_orders').read()[0]
		domain = [('message_partner_ids', 'child_of', [self.partner_id.commercial_partner_id.id])]
		order = self.env['sale.order'].search(domain)
		if order:
			action['domain'] = [('id', 'in', order.ids)]
			action['name'] = "Products"
		return action

	def crm_lead_action_pipeline(self):
		action = self.env.ref('crm.crm_lead_all_leads').read()[0]
		user = self.env['res.users'].search([('partner_id', '=', self.partner_id.id)]).ids
		leads = self.env['crm.lead'].sudo().search([('user_id', 'in', user)])
		action['domain'] = [('id', 'in', leads.ids)]
		return action


	def dealership_contract_action(self):
		action = self.env.ref('dealership_management.dealership_contract_action').read()[0]
		contract = self.get_related_contract()
		if contract:
			action['domain'] = [('id', '=', contract.id)]
		return action


	def set_state_date(self):
		for rec in self:
			if rec.state == 'pending':
				rec.date_pending = fields.Date.today()
			elif rec.state == 'submitted':
				rec.date_submitted = fields.Date.today()
			elif rec.state == 'processed':
				rec.date_processed = fields.Date.today()
			elif rec.state == 'approved':
				rec.date_approved = fields.Date.today()
			elif rec.state == 'decline':
				rec.date_declined = fields.Date.today()
			elif rec.state == 'done':
				rec.date_done = fields.Date.today()


	def _compute_currency_id(self):
		main_company = self.env['res.company']._get_main_company()
		for template in self:
			template.currency_id = main_company.currency_id.id


	advertisement_code  	= fields.Char('Advertisement code')
	birth_date          	= fields.Date('Date of Birth')
	business_type       	= fields.Many2one('business.type','Business Type')
	current_business    	= fields.Text('Current Business Details')
	date_pending        	= fields.Date('Pending on',readonly=True)
	date_submitted      	= fields.Date('Submitted on',readonly=True)
	date_processed      	= fields.Date('Processed on',readonly=True)
	date_approved       	= fields.Date('Approved on',readonly=True)
	date_declined       	= fields.Date('Declined on',readonly=True)
	date_done           	= fields.Date('Done on',readonly=True)
	enquiry_description 	= fields.Text('Enquiry Description')
	has_code            	= fields.Boolean('Do you have an Advertisement code?')
	has_xp              	= fields.Boolean('Do you have any experience in our business?')
	history_count       	= fields.Integer(compute=_get_history_count,readonly=True,tracking=True)
	interested_city     	= fields.Char('City Interested In:')
	investment_low_cap  	= fields.Float('From')
	investment_high_cap 	= fields.Float('To')
	partner_id          	= fields.Many2one('res.partner','Dealer',readonly=True)
	plan_id             	= fields.Many2one('dealership.plan','Plan Selected')
	plan_ids            	= fields.Many2many('dealership.plan',string='Plans Offered')
	# product_ids          	= fields.Many2many('product.product',string='Products')
	product_count       	= fields.Integer(compute=_get_product_count,readonly=True,tracking=True)
	order_count       		= fields.Integer(compute=_get_order_count,readonly=True,tracking=True)
	qualification      		= fields.Char('Qualification')
	token               	= fields.Char(readonly=True)
	total_area          	= fields.Float('Total Area (In Sq Ft):')
	turnover                = fields.Float('Last One Year Turnover?')
	xp_description          = fields.Text('If Yes,Brief History business you have done so far.')
	interested_state_id     = fields.Many2one(comodel_name='res.country.state', string='State Interested In:', ondelete='restrict')
	interested_country_id   = fields.Many2one('res.country', 'Country Interested In:', ondelete='restrict')
	business_xp_ids         = fields.One2many(comodel_name='business.experience', inverse_name='application_id', string='Business Experience')
	application_history_ids = fields.One2many(comodel_name='application.history', inverse_name='application_id', string='Application History')
	currency_id = fields.Many2one('res.currency', 'Currency', compute='_compute_currency_id')
	# rating counts of dealer
	total_rating_count = fields.Integer(string='Total Rating Count', default=0)
	app_avg_rating = fields.Float(string='Avg Rating', default=0)
	active = fields.Boolean('Active', default=True, help="If unchecked, it will allow you to hide the dealer without removing it.")
	state = fields.Selection(string='Status', default='submitted', inverse=set_state_date, tracking=True,
		selection = [
			('submitted','Submitted'),
			('processed','Processed'),
			('pending','Pending'),
			('approved','Approved'),
			('decline','Declined'),
			('done','Done'),
	])
	current_occupation = fields.Selection(string='Current Occupation',
		selection=[
			('self_employed','Self Employed'),
			('salaried','Salaried'),
			('others','Others')
	])
	vacancy_through = fields.Selection(string='Dealer Vacancy Known Through',
		selection=[
			('advertisement','Advertisement'),
			('area_sales_manager','Area Sales Manager'),
			('regional_manager','Regional Manager'),
			('website','Website'),
			('others','Others'),
	])
	site_available = fields.Selection(string='Available',
		selection=[
			('owned','Owned'),
			('rented','Rented'),
			('leased','Leased'),
			('others','Others'),
	])
	site_not_available = fields.Selection(string='Not Available',
		selection=[
			('to_be_purchased','To be purchased'),
			('to_be_rented','To be rented'),
			('to_be_leased','To be leased'),
	])
	stock_available = fields.One2many(comodel_name='dealer.stock.line', inverse_name='application_id', string='Available Stock')

	_unique_token = models.Constraint(
		'unique (token)','Token genrated is already exists.'
	)

	@api.model_create_multi
	def create(self, vals_list):
		for vals in vals_list:
			vals['token'] = random_token()
		return super().create(vals_list)

	def action_view_history(self):
		applications = self.mapped('application_history_ids')
		action = self.env.ref('dealership_management.application_history_action').read()[0]
		if len(applications) > 1:
			action['domain'] = [('id','in',applications.ids)]
		elif len(applications) == 1:
			action['views'] = [(self.env.ref('dealership_management.application_history_form_view').id,'form')]
			action['res_id'] = applications.ids[0]
		return action

	# used to send status link to client and set state
	def set_state(self, state=False):
		self.ensure_one()
		if state:
			self.state = state
		mail_template = self.env.ref('dealership_management.dealership_appication_status').sudo()
		mail_template.send_mail(self.id,force_send=True)

	def processed(self):
		self.ensure_one()
		message = False
		IrConfigParameter = self.env['ir.config_parameter'].sudo()
		if IrConfigParameter.get_param('dealership_management.allow_dealer_application') != 'creation_time':
			res = self.prepare_app_to_dealer_rule()
			if res:
				self.set_state('decline')
				message = _('decline')
		if not message:
			self.set_state('processed')
			message = _('processed')
		NotificationMessage.update({'message': _('Application marked as {}'.format(message))})
		self.env['bus.bus']._sendone((self._cr.dbname,'res.partner',self.env.user.partner_id.id), 'mail.simple_notification', NotificationMessage)

	def approve(self):
		self.ensure_one()
		plans_form = self.env.ref('dealership_management.application_plans_allocations', False)
		return {
			'name'     : _('Allocate Plans'),
            'type'     : 'ir.actions.act_window',
            'res_model': 'application.plan.allocation',
            'views'    : [(plans_form.id, 'form')],
			'target'   : 'new'
		}

	def create_history(self):
		action = self.env.ref('dealership_management.create_application_history_action').read()
		if action:
			action = action[0]
			return action

	def decline(self):
		self.ensure_one()
		self.state = 'decline'
		NotificationMessage.update({'message': _('Application marked as declined')})
		self.env['bus.bus']._sendone((self._cr.dbname,'res.partner',self.env.user.partner_id.id), 'mail.simple_notification', NotificationMessage)

	def get_unique_user_name(self, name):
		user_sudo = self.env['res.users'].sudo().search([('login', '=', name)])
		if user_sudo:
			data = name.split("@")
			name = (str(self.id)+"@").join(data)
			name = self.get_unique_user_name(name)
		return name

	def get_related_contract(self):
		return self.env['dealership.contract'].search([('application_id', '=', self.id)], limit=1)

	def create_res_partner(self, lang=False):
		self.ensure_one()
		try:
			vals = {
				'name': self.name,
				'email': self.email,
				'phone': self.phone,
				'street': self.street,
				'street2': self.street2,
				'zip': self.zip,
				'city': self.city,
				'application_id': self.id
			}

			if self.country_id:
				vals['country_id'] = self.country_id.id
			if self.state_id:
				vals['state_id'] = self.state_id.id

			if lang:
				supported_lang_codes = [code for code, _ in self.env['res.lang'].get_installed()]
				if lang in supported_lang_codes:
					vals['lang'] = lang

			Partner = self.env['res.partner'].sudo().create(vals)
			return Partner
		except Exception as e:
			_log.info("Partner Exception: %r", e)
			return False



	def create_user(self, lang=False):
		self.ensure_one()
		try:
			partner_id = self.partner_id
			if not partner_id:
				partner_id = self.create_res_partner(lang)

			name = self.get_unique_user_name(self.email)
			vals = {
				'login': name,
				'password': self.email,
				'partner_id': partner_id.id
			}

			User = self.env['res.users'].sudo()._create_user_from_template(vals)
			User.reset_password(User.login)
			return User

		except Exception as e:
			_log.info("User Exception: %r", e)
			return False

	def get_ratings(self):
		self.ensure_one()
		res = {}
		if self.app_avg_rating:
			val = self.app_avg_rating
			decimal = (val - math.floor(val))
			decimal = round(decimal,1)
			if decimal == .5:
				val = math.floor(val) + .5
			elif (decimal < .3) or (decimal > .7):
				val = round(val)
			else:
				val = math.floor(val) + .5
			res.update({
				'val_integer': math.floor(val),
				'val_decimal':  val - math.floor(val),
				'empty_star':  5 - ( math.floor(val)+math.ceil(val - math.floor(val))),
				'avg': self.app_avg_rating,
				'count': self.total_rating_count
			})
		return res

	def get_product_stock(self, product_id):
		self.ensure_one()
		stock = self.env['dealer.stock.line'].search([
			('product_id', '=', product_id),
			('application_id', '=', self.id)
		], limit=1)
		return stock


	def prepare_app_to_dealer_rule(self, app={}):
		_rule = self.env.ref('dealership_management.template_dealer_id')
		_ignore_field = ['rating_percentage_satisfaction']
		res = []
		app = app and app or self
		if _rule and app:
			# for field_name in _rule.fields_get_keys():
			for field_name in _rule._fields:
				value = getattr(_rule, field_name)
				target_value = app.get(field_name, 0) if isinstance(app, dict) else getattr(app, field_name)
				field_type = _rule._fields[field_name]
				if field_name not in _ignore_field:
					_is_float = isinstance(field_type, fields.Float)
					# _is_selection = isinstance(field_type, fields.Selection)
					# _is_char = isinstance(field_type, fields.Char)

					if (_is_float and value) and (not target_value or float(target_value) < value):
						res.append(
							('Integer', 'Field {} is empty either less than defined value {}.'.format(field_name, value))
						)

					# if _is_selection and value and not target_value:
					# 	res.append(
					# 		('Selection', 'Field {}  type is not empty.'.format(field_name))
					# 	)
					#
					# if _is_char and value and not target_value:
					# 	res.append(
					# 		('Char', 'Field {}  type is not empty.'.format(field_name))
					# 	)
		return res
