# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
from odoo import fields,models


class BusinessExperience(models.Model):
	_name = 'business.experience'
	_description = 'Business Experience'

	name           = fields.Char('Company Name')
	from_date      = fields.Date('From Date')
	to_date        = fields.Date('To Date')
	application_id = fields.Many2one('dealership.application')
