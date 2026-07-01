# -*- coding: utf-8 -*-

#################################################################################
# Author      : Cyder Solutions (<www.cyder.com.au>)
# Copyright(c): 2021-now
# All Rights Reserved.
#
# This module is copyright property of the author mentioned above.
# You can't redistribute/reshare/recreate it for any purpose.
#
#################################################################################

from odoo import api, models, fields

class ResPartner(models.Model):
    _inherit = "res.partner"


    site_contact = fields.Char(string="Site Contact")

    site_phone = fields.Char(string="Site Phone")

    equipment_count = fields.Integer(string="Equipment", compute='_compute_equipment_count')


    def _compute_equipment_count(self):
        for rec in self:
            rec.equipment_count = rec.env['equipment.details'].search_count([('client', '=', rec.id)])


    def listEquipment(self):
        return {
            'type': 'ir.actions.act_window',
            'name': 'Equipment',
            'view_mode': 'list,form',
            'res_model': 'equipment.details',
            'domain': [('client.id', '=', self.id)],
            'target': 'current',
        }


