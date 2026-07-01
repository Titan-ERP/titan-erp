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

from odoo import api, fields, models, _
import datetime


class EquipmentJobs(models.Model):
    _name = "equipment.jobs"
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = "Equipment Job"

    name = fields.Char('Job Number', default='New')
    assignee = fields.Many2one('hr.employee', string='Assignee', tracking=True)
    equipment = fields.Many2one('equipment.details', string='Equipment', tracking=True)
    location = fields.Char('Equipment Location')
    model = fields.Char('Model')
    serial_no = fields.Char('Serial Number', copy=False)
    asset_tag = fields.Char('Asset Tag', copy=False)
    issues = fields.Html(string="Issues")
    report = fields.Html('Report')
    task_id = fields.Integer('Task')
    address = fields.Html('Equipment Address')
    street = fields.Char('Street')
    street2 = fields.Char('Street2')
    zip = fields.Char('Zip')
    city = fields.Char('City')
    state = fields.Many2one("res.country.state", string='State')
    country = fields.Many2one('res.country', string='Country')
    service_date = fields.Date(string='Service Date', default=lambda self: fields.Datetime.today())
    note = fields.Html(string='Note')
    site_contact = fields.Char(string="Site Contact")
    site_phone = fields.Char(string="Site Phone")
    # warranty_expiration = fields.Date(string="Warranty Expiration")
    owner = fields.Many2one('res.partner', string="Owner")

    @api.onchange('equipment')
    def onchange_equipment(self):
        self.location = self.equipment.location
        self.serial_no = self.equipment.serial_no
        self.model = self.equipment.model
        self.street = self.equipment.street
        self.street2 = self.equipment.street2
        self.zip = self.equipment.zip
        self.city = self.equipment.city
        self.state = self.equipment.state
        self.country = self.equipment.country
        self.note = self.equipment.note
        # self.warranty_expiration = self.equipment.warranty_expiration
        self.owner = self.equipment.client
        self.asset_tag = self.equipment.asset_tag


    @api.onchange('serial_no')
    def onchange_serial_no(self):
        for row in self:
            row.equipment = self.env['equipment.details'].search([('serial_no', '=', row.serial_no)]).id























