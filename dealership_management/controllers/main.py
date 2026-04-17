# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
import logging
import json
from datetime import datetime
from odoo import fields, http, SUPERUSER_ID
from odoo.http import request, content_disposition
from odoo.addons.web.controllers.home import Home
from odoo.addons.website_sale.controllers.main import WebsiteSale
from odoo.addons.website.controllers.form import WebsiteForm
from odoo.addons.mail.controllers.thread import ThreadController
from odoo import tools, _
from odoo.tools import html2plaintext

_log = logging.getLogger(__name__)
MADATORY = ["name", "phone", "email", "street", "city", "country_id"]

class Website(Home):

	def remove_null_value_key(self, kw):
		'''delete all null vakue keys from dict'''

		for key in list(kw):
			if isinstance(kw[key], dict):
				up_val = self.remove_null_value_key(kw[key])
				kw[key] = up_val
			elif isinstance(kw[key], list):
				up_val = []
				for _ in kw[key]:
					if isinstance(_, dict):
						up_val.append(self.remove_null_value_key(_))
					else:
						up_val.append(_)
				kw[key] = up_val

			if not kw[key]:
				del kw[key]

		return kw


	def check_valid_field(self, vals):
		'''Check all required feild that need to fill an application'''

		error = []
		for field in MADATORY:
			if not vals.get(field):
				error.append({
					"name": _("Error"),
					"type": _("{} Field is not empty").format(field.capitalize())
				})

		if vals.get('email') and not tools.single_email_re.match(vals.get('email')):
			error.append({
				"name": _("Error"),
				"type": _("Invalid Email address {}").format(vals.get('email'))
			})

		if vals.get('email') and tools.single_email_re.match(vals.get('email')):
			app = request.env['dealership.application'].sudo().search([('email', '=', vals.get('email')),('state','!=','decline')])
			cond = app.partner_id != request.env.user.partner_id
			if app and cond:
				error.append({
					"name": _('Error'),
					"type": _("An application already have used this email address {}.").format(vals.get('email'))
				})

		IrConfigParameter = request.env['ir.config_parameter'].sudo()

		if IrConfigParameter.get_param('dealership_management.allow_dealer_application') == 'creation_time':
			dealer_app = request.env['dealership.application'].sudo()
			res = dealer_app.prepare_app_to_dealer_rule(vals)
			for _x in res:
				error.append({
					"name": _("Error"),
					"type": _(_x[1])
				})

		if not request.env.user._is_public() and request.env.user.application_id and request.env.user.application_id.state != 'done':
			error.append({
				"name": _("Error"),
				"type": _("You have alreday submit you application, please check you status now.")
			})

		return error


	@http.route(route='/dealer/application', type='http', auth="public", website=True)
	def application_form(self, **kw):
		try:

			application = request.env.user.sudo().application_id
			if application and application.state == 'done':
				return request.redirect('/application/dashboard')
			else:
				partner_id = request.env['res.partner']
				if request.env.uid != request.website.user_id.id:
					partner_id = request.env.user.partner_id

				params = {
                    "countries": request.env['res.country'].sudo().search([]),
					'business_type': request.env['business.type'].sudo().search([]),
					'partner_id': partner_id
                }
				return request.render('dealership_management.dealership_mangement_signup_form', params)

		except Exception:
			return request.env['ir.http']._handle_exception(404)


	@http.route(route='/dealer/create_application', type='jsonrpc', auth="public", website=True, csrf=False)
	def application_form_create(self, **kw):
		response = {"result": False}
		kw = self.remove_null_value_key(kw)
		error = self.check_valid_field(kw)

		if error:
			response.update({"template": error})
			return response

		if kw.get("business_xp_ids"):
			business_xp = []
			for data in kw.get("business_xp_ids"):
				id = request.env["business.experience"].sudo().create(data)
				business_xp.append(id.id)
			kw.update({
				"business_xp_ids": [(6, 0, business_xp)]
			})

		public = request.env.user._is_public()
		if not public:
			kw['partner_id'] = request.env.user.partner_id.id
		if kw.get('country_id'):
			kw.update({'country_id': int(kw.get('country_id', 0))})
		if kw.get('state_id'):
			kw.update({'state_id': int(kw.get('state_id', 0))})

		application_id = request.env["dealership.application"].sudo().create(kw)
		if not public:
			request.env.user.partner_id.application_id = application_id.id

		response.update({
			"result": True,
			"template": request.env['ir.ui.view']._render_template("dealership_management.sucess_membership",{})
		})
		if application_id:
			view_application = request.httprequest.host_url+"web#id="+str(application_id.id)+"&action="+str( application_id.env.ref("dealership_management.dealership_application_action").id)+"&model=dealership.application&view_type=form"
			value={'time': datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
					'view_application':view_application}
			mail_template = request.env.ref('dealership_management.dealership_appication_submission').sudo()
			mail_template.with_context(value).send_mail(application_id.id,force_send=True)

		return response

	@http.route(route='/dealer/status', type='http', csrf=False, auth="public", website=True, methods=['GET','POST'])
	def application_status(self, email='', request_id='', **kw):
		'''
		Used to dealership status login form and also status for users
		@param email, forgot_id, and not request_id send mail_template
		@param email and request_id, then show user status also use session_id for it
		'''
		param = {}
		session_id = request.session.get('application_id')

		if request.httprequest.method == 'GET' and session_id:
			request.session['application_id'] = False

		elif request.httprequest.method == 'POST' or session_id:

			if email and kw.get('forgot_id'):
				application_app = request.env["dealership.application"].sudo().search([('email', '=', email)], limit=1)
				if application_app:
					try:
						application_app.set_state()
						param['sent_email'] = True
					except Exception as e:
						param['sent_error_server'] = True
				else:
					param['sent_error'] = True

			else:
				if email and request_id:
					domain = [('token', '=', request_id), ('email', '=', email)]
				else:
					domain = [('id', '=', session_id)]
				application_app = request.env["dealership.application"].sudo().search(domain, limit=1)

				if application_app:
					param = {
						"model": application_app,
						"state": application_app.fields_get(["state"], ["selection"])["state"]["selection"],
						"history_id": application_app.application_history_ids,
					}
					request.session['application_id'] = application_app.id
					return request.render("dealership_management.dealer_status_form", {"application":param})
				else:
					request.session['application_id'] = False
					param['sent_error_token'] = True

		return request.render("dealership_management.dealer_dummy_status_form", param)



	@http.route(route='/dealer/add_history_attachment', type='jsonrpc', auth="public", website=True)
	def add_history_attachment(self, history=0, response=False, **kw):
		result = {"result": False}

		try:
			application_history = request.env['application.history'].sudo().browse(int(history))
			if application_history.exists():

				if application_history.ask_attachment and not kw.get("datas") or not response:
					raise ValueError("Incomplete data")

				application_history.response = response
				application_history.full_fill = True

				if kw and kw.get("datas"):
					kw.update({"datas": kw.get("datas").split(',')[1].strip()})
					kw.update({'access_token': application_history.application_id.token})
					IrAttachment = request.env['ir.attachment'].sudo().create(kw)
					application_history.app_attachment = IrAttachment.id
					data_url = '/web/content/{}?access_token={}'.format(IrAttachment.id, IrAttachment.access_token)
					result.update({'data_url': data_url})

				result.update({"result": True, 'query': application_history.query, 'ans': response})
			else:
				raise ValueError("Incomplete data")

		except Exception as e:
			result.update({"error": _("All Information has mandatory")})

		return result

	@http.route(route='/dealer/get_contract', type='jsonrpc', auth="public", website=True)
	def get_contract(self, application=0, plan=0, **kw):
		try:
			contract = False
			contract_id = request.env['dealership.contract'].sudo().search([('application_id', '=', application)], limit=1)
			plan = request.env['dealership.plan'].browse(int(plan))

			if plan.exists():
				if contract_id and contract_id.state == 'draft':
					contract_id.date_from = fields.Date.today()
					contract_id.application_id.plan_id = plan.id
					contract = contract_id
				elif contract_id:
					contract = contract_id
				else:
					application = request.env['dealership.application'].browse(int(application))
					if application.exists():
						contract = plan.generate_contracts(application.id)

		except Exception as e:
			_log.error("Application contract error: %r",e)

		return request.env["ir.ui.view"]._render_template('dealership_management.portal_application_contracts', {"contract": contract})



	@http.route(route='/dealer/get_contract_pdf/<int:contract_id>', type='http', auth="public", website=True)
	def get_contract_pdf(self, contract_id, access_token=None, report_type=None, download=True, **kw):
		try:
			contract_sudo = request.env['dealership.contract'].sudo().browse(contract_id)
			if contract_sudo.exists() and report_type == 'pdf':
				ReportAction = request.env['ir.actions.report'].sudo()
				method_name = '_render_qweb_%s' % (report_type)
				report = getattr(ReportAction, method_name)('dealership_management.action_report_contract_info', contract_sudo.ids, data={'report_type': report_type})[0]
				reporthttpheaders = [
					('Content-Type', 'application/pdf' if report_type == 'pdf' else 'text/html'),
					('Content-Length', len(report)),
				]
				if report_type == 'pdf' and download:
					filename = "Contract Details.pdf"
					reporthttpheaders.append(('Content-Disposition', content_disposition(filename)))
				return request.make_response(report, headers=reporthttpheaders)
		except Exception as e:
			_log.error("Contract PDf download error: %r", e)
		return request.redirect("/dealer/status")



	@http.route(route='/dealer/dealer_locator', csrf=False, type='http', auth="public", website=True, methods=['GET', 'POST'])
	def dealer_locator(self, offset=0, **kw):
		context = {}
		if request.httprequest.method == 'GET':
			plans = request.env['dealership.plan'].search([])
			context['plans'] = plans
			context['countries'] = request.env['res.country'].sudo().search([])
			context['res'] = {'empty_star': 5}
			return request.render('dealership_management.dealer_locator', context)
		else:
			kw = self.remove_null_value_key(kw)
			"""If user search dealers through his local location"""
			if kw.get('country_code'):
				country_id = request.env['res.country'].search([('code', '=', kw.get('country_code'))], limit=1)
				if country_id:
					kw['country_id'] = country_id.id
					state_id = request.env['res.country.state'].search([('name', 'ilike', kw.get('state_name')), ('country_id', '=', country_id.id)], limit=1)
					if state_id:
						kw['state_id'] = state_id.id
					del kw['country_code']
					del kw['state_name']

			"""Search dealers based on set params"""
			if kw.get('country_id'):
				try:
					apps, limit, offset = [], 4, int(offset)
					country_id = int(kw.get('country_id'))
					domain = [('state', '=', 'done'),('country_id', '=', country_id)]

					if kw.get('plan_id'):
						domain.append(('plan_id', '=', int(kw.get('plan_id'))))
					if kw.get('state_id'):
						domain.append(('state_id', '=', int(kw.get('state_id'))))
					if kw.get('city'):
						domain.append(('city', 'ilike', kw.get('city')))
					if kw.get('zip'):
						domain.append(('zip', 'ilike', kw.get('zip')))

					application_obj = request.env['dealership.application']

					applications = application_obj.sudo().search(domain, limit=limit, offset=offset)
					application_count = application_obj.sudo().search_count(domain)
					next = True if application_count > (offset+limit) else False
					prev = True if offset else False
					
					if applications:
						for app in applications:
							read_param = ['id', 'name', 'street', 'city', 'zip', 'phone', 'email', 'state_id', 'country_id']
							read_data = app.read(read_param)[0]
							read_data['image'] = request.website.image_url(app, 'image')
							partner_id = app.partner_id
							read_data['partner_id'] = partner_id.id

							if partner_id.partner_latitude and partner_id.partner_longitude:
								read_data['coords'] = {
									'lat': partner_id.partner_latitude,
									'lng': partner_id.partner_longitude
								}

							rating = {'res': app.get_ratings()}
							rating = request.env['ir.ui.view']._render_template('dealership_management.dealer_application_rating_star', rating)
							read_data['ratings'] = rating
							read_data['rating_count'] = app.total_rating_count
							apps.append(read_data)

						context['applications'] = apps
						context['next'] = next
						context['prev'] = prev
						context['offset'] = offset
						context['limit'] = limit
						context['domain'] = kw
				except Exception as e:
					_log.info("Dealership Error: %r", e)
			
			context = json.dumps(context)
			return request.make_response(context, headers=[('Content-Type', 'application/json')])


	@http.route(route='/dealer/faq', type='http', auth="public", website=True)
	def dealer_faqs(self, offset=0, **kw):
		faq_category = request.env['application.faq.category'].sudo().search([], order="sequence")
		active_cat = False
		if faq_category:
			active_cat = faq_category[0]
		return request.render('dealership_management.dealer_faqs', {"faq_category": faq_category, "active_cat": active_cat})


	@http.route(route='/dealer/faq_items', type='jsonrpc', auth="public", website=True)
	def dealer_faqs_items(self, **kw):
		context = {}
		faq_category = kw.get("faq_category",False)
		faq = kw.get("faq",False)
		search = kw.get("search",False)
		if faq:
			faq_line = request.env['application.faq.line'].sudo().browse(faq)
			if faq_line.exists():
				context['name'] = faq_line.name
				context['answer'] = faq_line.answer
				context['description'] = html2plaintext(faq_line.description) or ''
				context['id'] = faq_line.id
		else:
			faq_line = []
			read_param = []
			description = ''
			head = ''

			if faq_category:
				faq_category = request.env['application.faq.category'].sudo().browse(faq_category)
				faq_line =  request.env['application.faq.line'].sudo().search([('faq_category_id', '=', faq_category.id)], order="sequence")
				head = faq_category.name
				description = html2plaintext(faq_category.description) or ''
			if search:
				faq_line = request.env['application.faq.line'].sudo().search(['|',('answer', 'ilike', search), ('name', 'ilike', search)], order="sequence")
				head = search
				description = _("Your search results")
			if faq_line:
				read_param = faq_line.read(['id', 'name', 'answer'])
				for x in read_param:
					if len(x['answer']) > 250:
						x['answer'] = x['answer'][:247]
			context['faq_line'] = read_param
			context['head'] = head
			context['description'] = description

		return context

	@http.route('/check/active_locator',type='jsonrpc', auth='public',website=True)
	def check_active_locator(self):
		response = {'flag':False}
		allow_dealer_locator = request.website.allow_dealer_locator
		g_key = request.env['ir.config_parameter'].sudo().get_param('dealership_management.google_map_api_key')
		if g_key and allow_dealer_locator:
			response = {'gmap_api_key':g_key,'flag':True}
		return response



class WebsiteSale(WebsiteSale):

	@http.route(route='/dealer/buy_dealership_plan', type='http', auth="public", website=True, methods=['POST'])
	def buy_dealership_plan(self, plan=0, application=0, **kw):
		plan_id = request.env['dealership.plan'].sudo().browse(int(plan))
		application_id = request.env['dealership.application'].sudo().browse(int(application))

		if plan_id.exists() and application_id.exists():
			sale_order = request.cart or request.website._create_cart()

			if sale_order.order_line:
				sale_order.order_line.unlink()
			sale_order.with_context(skip_cart_verification=True)._cart_add(
                    product_id=plan_id.product_id.id,
                    quantity=1
                )

			if len(sale_order.order_line.ids) == 1:
				sale_order.order_line.is_dealer_application = True
				sale_order.order_line.application_id = application_id.id

			Partner = application_id.partner_id
			if not Partner and not request.env.user._is_public():
				Partner = request.env.user.partner_id
			if not Partner:
				Partner = application_id.create_res_partner(request.context.get('lang', False))
				application_id.partner_id = Partner.id

			sale_order.partner_shipping_id = Partner.id
			sale_order.partner_id = Partner.id
		return request.redirect("/shop/checkout?try_skip_step=true")

	@http.route()
	def shop_payment_confirmation(self, **post):
		response = super(WebsiteSale, self).shop_payment_confirmation(**post)
		try:
			if hasattr(response, 'qcontext'):
				sale_order = response.qcontext.get('order')
				if sale_order and sale_order.state != 'sale' and not sale_order.dealer_payment_status:
					order_line = sale_order.order_line.filtered(lambda x: x.is_dealer_application and x.application_id)
					if order_line:
						order_line = order_line[0]
						application_id = order_line.application_id
						plan_id = request.env['dealership.plan'].sudo().search([('product_id', '=', order_line.product_id.id)], limit=1)
						if plan_id and application_id:
							mail_template = request.env.ref('dealership_management.dealership_appication_confirmation_status').sudo()
							mail_template.send_mail(application_id.id,force_send=True)
					sale_order.dealer_payment_status = True
		except Exception as e:
			pass
		return response
	
	@http.route(['/shop/country_infos/<model("res.country"):country>'], type='jsonrpc', auth="public", methods=['POST'], website=True)
	def country_infos(self, country, **kw):
		country = request.env['res.country'].sudo().browse(int(country))
		return dict(
			states=[(st.id, st.name, st.code) for st in country.sudo().state_ids],
		)


class WebsiteForm(WebsiteForm):

	def extract_data(self, model, values):
		response = super(WebsiteForm, self).extract_data(model, values)
		if values.get('dealer_lead_call'):
			try:
				response['custom'] = ''
				user = request.env['res.users'].sudo().search([
					('partner_id', '=', int(request.params.get('partner_id', 0)))
				])
				response['record']['user_id'] = user.id
				if values.get('customer_id'):
					response['record']['partner_id'] = int(values.get('customer_id', 0))
			except Exception as e:
				pass
		return response

def _get_review_msg(msg):
	return True if msg.get('rating_value') else False
class ThreadControllerInherit(ThreadController):

	@http.route()
	def mail_message_post(self, thread_model, thread_id, post_data, context=None, **kwargs):
		result = super(ThreadControllerInherit, self).mail_message_post(
			thread_model=thread_model, thread_id=thread_id, post_data=post_data, context=context, **kwargs)
		if thread_model == 'dealership.application' and post_data.get('rating_value'):
			application = request.env['dealership.application'].sudo().browse(int(thread_id))
			if application.exists():
				rate = 0
				try:
					rate = float(post_data.get('rating_value'))
				except Exception as e:
					rate = 0
				if rate:
					increment = (rate - application.app_avg_rating)/(application.total_rating_count + 1)
					application.app_avg_rating = round((application.app_avg_rating + increment), 1)
					application.total_rating_count += 1

				return {
					'temp': request.env['ir.ui.view']._render_template('dealership_management.dealer_application_rating_star', {'res': application.get_ratings()}),
					'count': application.total_rating_count
				}
		return result

	@http.route('/dealer/review/messages', type='jsonrpc', auth='public', website=True)
	def dealer_review_messages(self, thread_model, thread_id, limit=30, count=0):
		domain = [
            ("res_id", "=", int(thread_id)),
            ("model", "=", thread_model),
            ("message_type", "!=", "user_notification"),
        ]
		res = request.env["mail.message"].sudo()._message_fetch(domain,
				search_term=None, before=None, after=None, around=None, limit=limit)
		messages = res.pop("messages").read()
		result = []
		review_msg = list(filter(_get_review_msg, messages))
		chunk_size = 5
		msgs = [review_msg[i:i + chunk_size] for i in range(0, len(review_msg), chunk_size)]
		for msg in msgs[count]:
			formatted_date = msg.get('create_date').strftime("%b %dth %Y, %I:%M:%S %p")
			day = msg.get('create_date').day
			suffix = 'th'
			if day in [1, 21, 31]:
				suffix = 'st'
			elif day in [2, 22]:
				suffix = 'nd'
			elif day in [3, 23]:
				suffix = 'rd'
			formatted_date = formatted_date.replace('th', suffix)
			res_msg = {
				'message': msg.get('body'),
				'date': formatted_date,
				'name': msg.get('author_id')[1],
				'rating_value': int(msg.get('rating_value')),
				'author_avatar_url': f"/web/image/mail.message/{msg.get('id')}/author_avatar/50x50",
			}
			result.append(res_msg)
		data = {
			'review_data': request.env['ir.ui.view']._render_template("dealership_management.dealer_see_all_reviews",{
				"all_reviews" : result
			}),
			'is_load_more' : True if count+1 < len(msgs) else False
		}
		return data
