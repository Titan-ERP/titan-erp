# -*- coding: utf-8 -*-

#################################################################################
# Author      : Cyder Solutions (<www.cyder.com.au>)                            #
# Copyright(c): 2018-present                                                    #
# All Rights Reserved.                                                          #
#                                                                               #
# This module is copyright property of the author mentioned above.              #
# You can't redistribute/reshare/recreate it for any purpose.                   #
#################################################################################

from odoo import api, fields, models, _


class EquipmentSystems(models.Model):
    _name = "equipment.systems"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "Equipment Systems"

    name = fields.Char('System Name', required=True, translate=True)
    client_id = fields.Many2one('res.partner', string='Client', tracking=True)
    equipment_ids = fields.Many2many(
        'equipment.details',
        'equipment_system_equipment_rel',
        'system_id',
        'equipment_id',
        string="Equipment",
        copy=False,
    )
