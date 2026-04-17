# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
from odoo import fields, models


class Partner(models.Model):
	_inherit = 'res.partner'

	application_id = fields.Many2one(comodel_name='dealership.application')

class MailThread(models.AbstractModel):
	_inherit = 'mail.thread'

	def _get_redirect_url(self, model):
		if model._name == 'crm.lead':
			if model.user_id and model.user_id.application_id:
				return '/application/dashboard'
		return '/mail/view?model=%s&amp;res_id=%s' % (model._name, model.id)
