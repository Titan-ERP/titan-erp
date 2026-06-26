# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class RentalInspection(models.Model):
    _name = 'rental.inspection'
    _description = 'Equipment Inspection'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Reference', required=True, copy=False, readonly=True,
                       default=lambda self: _('New'), tracking=True)
    rental_order_id = fields.Many2one('sale.order', string='Rental Order', required=True,
                                      tracking=True, domain=[('is_rental_order', '=', True)])
    partner_id = fields.Many2one('res.partner', related='rental_order_id.partner_id',
                                 string='Customer', store=True)
    company_id = fields.Many2one('res.company', related='rental_order_id.company_id',
                                 string='Company', store=True)
    product_id = fields.Many2one('product.product', string='Equipment', required=True,
                                 domain=[('rent_ok', '=', True)], tracking=True)
    product_category_id = fields.Many2one('product.category', related='product_id.categ_id',
                                          string='Equipment Category', store=True)

    # Unit identification
    lot_id = fields.Many2one(
        'stock.lot',
        string='Serial Number',
        tracking=True,
        domain="[('product_id', '=', product_id)]",
        compute='_compute_lot_id',
        store=True,
        recursive=True,
    )

    @api.depends('previous_inspection_id', 'previous_inspection_id.lot_id')
    def _compute_lot_id(self):
        for inspection in self:
            if inspection.type == 'return' and inspection.previous_inspection_id:
                inspection.lot_id = inspection.previous_inspection_id.lot_id
            # pickup inspections: leave untouched (readonly=False allows manual entry)
    unit_number = fields.Integer(string='Unit #', default=0,
                                 help='Sequential unit number linking pickup and return inspections')
    unit_label = fields.Char(string='Unit', compute='_compute_unit_label', store=True)

    type = fields.Selection([
        ('pickup', 'Pickup Inspection'),
        ('return', 'Return Inspection')
    ], string='Type', required=True, tracking=True)
    date = fields.Datetime(string='Inspection Date', required=True, default=fields.Datetime.now,
                           tracking=True)
    equipment_hours = fields.Float(string='Equipment Hours/Odometer', tracking=True)

    # Set at creation time by _auto_create_inspection_drafts — no lookup ever needed.
    previous_inspection_id = fields.Many2one(
        'rental.inspection',
        string='Previous Inspection',
        store=True,
        tracking=True,
    )
    hours_usage = fields.Float(
        string='Usage',
        compute='_compute_hours_usage',
        store=True,
        help='Hours/odometer consumed during this rental '
             '(return reading minus pickup reading)',
    )

    inspector_id = fields.Many2one('res.users', string='Inspector', required=True,
                                   default=lambda self: self.env.user, tracking=True)
    state = fields.Selection([
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('done', 'Completed'),
        ('cancelled', 'Cancelled')
    ], string='Status', default='draft', tracking=True)

    checklist_ids = fields.One2many('rental.inspection.checklist', 'inspection_id',
                                    string='Checklist Items')
    note = fields.Html(string='Notes')
    damage_found = fields.Boolean(string='Damage Found', tracking=True)
    damage_description = fields.Html(string='Damage Description')

    customer_signature = fields.Binary(string='Customer Signature', attachment=True)
    customer_name = fields.Char(string='Customer Name')
    customer_signature_date = fields.Datetime(string='Customer Signature Date')

    inspector_signature = fields.Binary(string='Inspector Signature', attachment=True)
    inspector_name_id = fields.Many2one('res.users', string='Inspector Name',
                                        default=lambda self: self.env.user)
    inspector_signature_date = fields.Datetime(string='Inspector Signature Date')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('rental.inspection') or _('New')

            if vals.get('rental_order_id') and not vals.get('customer_name'):
                rental_order = self.env['sale.order'].browse(vals['rental_order_id'])
                vals['customer_name'] = rental_order.partner_id.name

            if not vals.get('customer_signature_date'):
                vals['customer_signature_date'] = fields.Datetime.now()
            if not vals.get('inspector_signature_date'):
                vals['inspector_signature_date'] = fields.Datetime.now()

        return super().create(vals_list)

    @api.onchange('rental_order_id')
    def _onchange_rental_order(self):
        if self.rental_order_id and not self.customer_name:
            self.customer_name = self.rental_order_id.partner_id.name

    @api.depends('lot_id', 'unit_number')
    def _compute_unit_label(self):
        for inspection in self:
            if inspection.lot_id:
                inspection.unit_label = inspection.lot_id.name
            elif inspection.unit_number:
                inspection.unit_label = _('Unit %d') % inspection.unit_number
            else:
                inspection.unit_label = ''

    @api.depends('type', 'equipment_hours',
                 'previous_inspection_id', 'previous_inspection_id.equipment_hours')
    def _compute_hours_usage(self):
        for inspection in self:
            if (inspection.type == 'return'
                    and inspection.previous_inspection_id
                    and inspection.previous_inspection_id.equipment_hours is not False):
                inspection.hours_usage = (
                    inspection.equipment_hours
                    - inspection.previous_inspection_id.equipment_hours
                )
            else:
                inspection.hours_usage = 0.0

    def action_start_inspection(self):
        self.ensure_one()
        if not self.checklist_ids:
            self._create_checklist_items()
        self.write({'state': 'in_progress'})

    def action_complete_inspection(self):
        self.ensure_one()
        self.write({'state': 'done'})

    def action_cancel_inspection(self):
        self.write({'state': 'cancelled'})

    def action_view_rental_order(self):
        self.ensure_one()
        return {
            'name': _('Rental Order'),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'res_id': self.rental_order_id.id,
            'view_mode': 'form',
            'target': 'current',
        }

    def _create_checklist_items(self):
        self.ensure_one()
        template = (
            self.product_category_id.pickup_template_id
            if self.type == 'pickup'
            else self.product_category_id.return_template_id
        )
        if not template:
            raise UserError(_(
                'No inspection template found for this equipment category and inspection type.'
            ))

        self.checklist_ids.unlink()

        vals_list = [{
            'inspection_id': self.id,
            'name': item.name,
            'description': item.description,
            'sequence': item.sequence,
            'type': item.type,
            'require_photo': item.require_photo,
            'require_note': item.require_note,
        } for item in template.item_ids]

        if vals_list:
            self.env['rental.inspection.checklist'].create(vals_list)
