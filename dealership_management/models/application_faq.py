# -*- coding: utf-8 -*-
##############################################################################
# Copyright (c) 2015-Present Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# See LICENSE file for full copyright and licensing details.
# License URL : <https://store.webkul.com/license.html/>
##############################################################################
from odoo import fields, models

class ApplicationFAQ(models.Model):
    _name = 'application.faq.category'
    _description = 'FAQ Category'

    name = fields.Char(required=True)
    description = fields.Char(string="Description")
    sequence = fields.Integer('Sequence')
    image = fields.Image("Image")
    faq_line = fields.One2many(comodel_name='application.faq.line', inverse_name='faq_category_id', string='Frequently Asked Questions')


class ApplicationFAQLine(models.Model):
    _name = 'application.faq.line'
    _description = 'Frequently Asked Questions'

    name = fields.Text(required=True, string="Question")
    answer = fields.Text(required=True, string="Answer")
    description = fields.Html(string="Description")
    sequence = fields.Integer('Sequence')
    faq_category_id = fields.Many2one('application.faq.category', required=True)
