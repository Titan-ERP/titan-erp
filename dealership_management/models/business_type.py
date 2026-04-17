# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
from odoo import fields,models


class BusinessType(models.Model):
	_name = 'business.type'
	_description = 'Business Type'

	name = fields.Char('Type Name')
	code = fields.Char('Type Code')
