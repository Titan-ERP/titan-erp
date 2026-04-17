# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
from odoo import api, models, fields
from odoo import exceptions
import logging

_log = logging.getLogger(__name__)

class CreateApplicationHistory(models.TransientModel):
	_name = 'create.application.history'
	_description = 'Application History Wizard'

	query = fields.Text(required=True)
	ask_attachment = fields.Boolean()

	def confirm(self):
		active_id = self.env.context.get('active_id')
		application = self.env['dealership.application'].sudo().browse(active_id)

		if application.exists() and application.state != 'approved':
			vals = {
					'application_id': active_id,
					'query'         : self.query,
					'ask_attachment': self.ask_attachment,
					'state'			: application.state
				}
			self.env['application.history'].sudo().create(vals)
			application.state = 'pending'
			mail_template = self.env.ref('dealership_management.dealership_appication_status')
			mail_template.send_mail(application.id, force_send=True)
		else:
			raise exceptions.ValidationError("Application has approved, You can't create more history.")


class PlanAllocation(models.TransientModel):
	_name = 'application.plan.allocation'
	_description = 'Allocate Plan to the client when setiing application state to approved'

	application_plans = fields.Many2many('dealership.plan', string='Plans Offered', required=True)

	def allocate(self):
		active_id = self.env.context.get('active_id')
		application = self.env['dealership.application'].browse(active_id)
		if application.exists():
			application.plan_ids = [(6, 0, self.application_plans.ids)]
			application.set_state("approved")
