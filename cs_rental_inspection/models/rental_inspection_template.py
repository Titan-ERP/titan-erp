# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError

class RentalInspectionTemplate(models.Model):
    _name = 'rental.inspection.template'
    _description = 'Inspection Template'
    _inherit = ['mail.thread']
    _order = 'sequence, id'

    sequence = fields.Integer(string='Sequence', default=10)
    name = fields.Char(string='Template Name', required=True, tracking=True)
    company_id = fields.Many2one('res.company', string='Company',
                              default=lambda self: self.env.company)
    type = fields.Selection([
        ('pickup', 'Pickup Template'),
        ('return', 'Return Template')
    ], string='Template Type', required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    description = fields.Html(string='Description')
    item_ids = fields.One2many('rental.inspection.template.item', 'template_id', 
                            string='Checklist Items')

class RentalInspectionTemplateItem(models.Model):
    _name = 'rental.inspection.template.item'
    _description = 'Inspection Template Item'
    _order = 'sequence, id'

    template_id = fields.Many2one('rental.inspection.template', string='Template',
                               required=True, ondelete='cascade')
    sequence = fields.Integer(string='Sequence', default=10)
    name = fields.Char(string='Item', required=True, translate=True)
    type = fields.Selection([
        ('condition', 'Condition Check'),
        ('damage', 'Damage Check'),
        ('safety', 'Safety Check'),
        ('operation', 'Operation Check'),
        ('cleanliness', 'Cleanliness Check'),
        ('measurement', 'Measurement'),
        ('other', 'Other')
    ], string='Check Type', required=True, default='condition')
    description = fields.Text(string='Description', translate=True)
    require_photo = fields.Boolean(string='Require Photo')
    require_note = fields.Boolean(string='Require Note')

    @api.constrains('name')
    def _check_name(self):
        for item in self:
            if len(item.name.strip()) < 3:
                raise ValidationError(_('Template item name must be at least 3 characters long'))