# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import api, fields, models, _

class RentalInspectionChecklist(models.Model):
    _name = 'rental.inspection.checklist'
    _description = 'Inspection Checklist Item'
    _order = 'sequence, id'

    inspection_id = fields.Many2one('rental.inspection', string='Inspection', required=True,
                                  ondelete='cascade')
    sequence = fields.Integer(string='Sequence', default=10)
    name = fields.Char(string='Item', required=True)
    description = fields.Text(string='Description')
    type = fields.Selection([
        ('condition', 'Condition Check'),
        ('damage', 'Damage Check'),
        ('safety', 'Safety Check'),
        ('operation', 'Operation Check'),
        ('cleanliness', 'Cleanliness Check'),
        ('measurement', 'Measurement'),
        ('other', 'Other')
    ], string='Check Type', required=True, default='condition')
    
    result = fields.Selection([
        ('pass', 'Pass'),
        ('fail', 'Fail'),
        ('na', 'Not Applicable')
    ], string='Result', default='na', required=True)
    measurement = fields.Float(string='Measurement Value')
    measurement_uom = fields.Many2one('uom.uom', string='Unit of Measure')
    require_photo = fields.Boolean(string='Photo Required')
    require_note = fields.Boolean(string='Note Required')
    photo = fields.Binary(string='Photo', attachment=True)
    photo_name = fields.Char(string='Photo Name')
    note = fields.Text(string='Notes')
    
    previous_item_id = fields.Many2one('rental.inspection.checklist', string='Previous Check',
                                     compute='_compute_previous_item', store=True)
    previous_result = fields.Selection(related='previous_item_id.result', string='Previous Result')
    
    @api.depends('inspection_id.previous_inspection_id', 'name')
    def _compute_previous_item(self):
        for item in self:
            if item.inspection_id.previous_inspection_id:
                previous = self.search([
                    ('inspection_id', '=', item.inspection_id.previous_inspection_id.id),
                    ('name', '=', item.name)
                ], limit=1)
                item.previous_item_id = previous.id
            else:
                item.previous_item_id = False