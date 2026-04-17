# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
from odoo import fields,models


class ApplicationHistory(models.Model):
	_name = 'application.history'
	_description = 'Application History'
	_rec_name = 'state'

	application_id = fields.Many2one('dealership.application',required=True)
	query          = fields.Text('Query')
	ask_attachment = fields.Boolean('Ask For Attachment')
	to_date        = fields.Date('To Date')
	response       = fields.Text('Response')
	app_attachment = fields.Many2one('ir.attachment', string="Attachment")
	full_fill      = fields.Boolean(default=False)
	state = fields.Selection(string='Status', default='submitted', readonly=True,
		selection = [
			('submitted','Submitted'),
			('pending','Pending'),
			('processed','Processed'),
			('approved','Approved'),
			('decline','Declined'),
			('done','Done'),
	])
