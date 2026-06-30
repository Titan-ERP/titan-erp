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
        self.previous_item_id = False
        by_prev_insp = {}
        for item in self:
            prev_insp = item.inspection_id.previous_inspection_id
            if prev_insp:
                by_prev_insp.setdefault(prev_insp.id, []).append(item)
        for prev_insp_id, items in by_prev_insp.items():
            names = list({item.name for item in items})
            previous_checks = self.search([
                ('inspection_id', '=', prev_insp_id),
                ('name', 'in', names),
            ])
            name_map = {}
            for check in previous_checks:
                name_map.setdefault(check.name, check)
            for item in items:
                item.previous_item_id = name_map.get(item.name, False)