# -*- coding: utf-8 -*-
from odoo import fields, models


class ProjectTask(models.Model):
    _inherit = 'project.task'

    dmc_equipment = fields.Char(string='Equipment', tracking=True)
    dmc_serial_number = fields.Char(string='Serial Number', tracking=True)
    dmc_equipment_run_hours = fields.Integer(string='Equipment Run Hours', tracking=True)
