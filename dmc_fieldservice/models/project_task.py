# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ProjectTask(models.Model):
    _inherit = 'project.task'

    dmc_equipment = fields.Char(string='Equipment', tracking=True)
    dmc_serial_number = fields.Char(string='Serial Number', tracking=True)
    dmc_equipment_run_hours = fields.Integer(string='Equipment Run Hours', tracking=True)

    @api.model
    def default_get(self, fields_list):
        result = super().default_get(fields_list)
        parent_id = result.get('parent_id') or self.env.context.get('default_parent_id')
        if parent_id:
            parent = self.browse(parent_id)
            if 'dmc_equipment' in fields_list:
                result['dmc_equipment'] = parent.dmc_equipment
            if 'dmc_serial_number' in fields_list:
                result['dmc_serial_number'] = parent.dmc_serial_number
            if 'dmc_equipment_run_hours' in fields_list:
                result['dmc_equipment_run_hours'] = parent.dmc_equipment_run_hours
        return result

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            parent_id = vals.get('parent_id')
            if not parent_id:
                continue
            parent = self.browse(parent_id)
            if not vals.get('dmc_equipment'):
                vals['dmc_equipment'] = parent.dmc_equipment
            if not vals.get('dmc_serial_number'):
                vals['dmc_serial_number'] = parent.dmc_serial_number
            if not vals.get('dmc_equipment_run_hours'):
                vals['dmc_equipment_run_hours'] = parent.dmc_equipment_run_hours
        return super().create(vals_list)

    @api.constrains('dmc_equipment', 'dmc_serial_number', 'dmc_equipment_run_hours', 'project_id')
    def _check_dmc_equipment_fields_required(self):
        for task in self:
            if not task.is_fsm:
                continue
            missing = []
            if not task.dmc_equipment:
                missing.append(_('Equipment'))
            if not task.dmc_serial_number:
                missing.append(_('Serial Number'))
            if not task.dmc_equipment_run_hours:
                missing.append(_('Equipment Run Hours'))
            if missing:
                raise ValidationError(_(
                    'The following fields are required on Field Service tasks:\n%s'
                ) % '\n'.join(f'  • {f}' for f in missing))
