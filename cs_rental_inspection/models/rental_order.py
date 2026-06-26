# -*- coding: utf-8 -*-
# Copyright (C) 2024 Cyder Solutions - All Rights Reserved

from odoo import api, fields, models, _


class RentalOrder(models.Model):
    _inherit = 'sale.order'

    inspection_ids = fields.One2many('rental.inspection', 'rental_order_id', string='Inspections')

    # --- Summary counts ---
    inspection_count = fields.Integer(compute='_compute_inspection_counts', store=True)
    pickup_inspections_done = fields.Integer(compute='_compute_inspection_counts', store=True)
    pickup_inspections_total = fields.Integer(compute='_compute_inspection_counts', store=True)
    return_inspections_done = fields.Integer(compute='_compute_inspection_counts', store=True)
    return_inspections_total = fields.Integer(compute='_compute_inspection_counts', store=True)

    # --- x/y progress labels for stat buttons ---
    pickup_progress_label = fields.Char(
        compute='_compute_progress_labels', store=True,
        string='Pickup Progress')
    return_progress_label = fields.Char(
        compute='_compute_progress_labels', store=True,
        string='Return Progress')

    # --- Control flags ---
    has_inspection_required = fields.Boolean(
        compute='_compute_has_inspection_required', store=True)
    has_pending_pickup = fields.Boolean(
        compute='_compute_inspection_state_flags', store=True)
    has_pending_return = fields.Boolean(
        compute='_compute_inspection_state_flags', store=True)
    has_completed_pickups = fields.Boolean(
        compute='_compute_inspection_state_flags', store=True)

    # -------------------------------------------------------------------------
    # Compute methods
    # -------------------------------------------------------------------------

    @api.depends('order_line.product_id', 'order_line.product_id.categ_id',
                 'order_line.is_rental')
    def _compute_has_inspection_required(self):
        for order in self:
            rental_lines = order.order_line.filtered('is_rental')
            order.has_inspection_required = any(
                line.product_id.categ_id.pickup_template_id
                or line.product_id.categ_id.return_template_id
                for line in rental_lines
            )

    @api.depends('inspection_ids', 'inspection_ids.state')
    def _compute_inspection_counts(self):
        for order in self:
            pickups = order.inspection_ids.filtered(
                lambda i: i.type == 'pickup' and i.state != 'cancelled'
            )
            returns = order.inspection_ids.filtered(
                lambda i: i.type == 'return' and i.state != 'cancelled'
            )
            order.inspection_count = len(pickups) + len(returns)
            order.pickup_inspections_total = len(pickups)
            order.pickup_inspections_done = len(pickups.filtered(lambda i: i.state == 'done'))
            order.return_inspections_total = len(returns)
            order.return_inspections_done = len(returns.filtered(lambda i: i.state == 'done'))

    @api.depends('pickup_inspections_done', 'pickup_inspections_total',
                 'return_inspections_done', 'return_inspections_total')
    def _compute_progress_labels(self):
        for order in self:
            order.pickup_progress_label = '%d / %d' % (
                order.pickup_inspections_done,
                order.pickup_inspections_total,
            )
            order.return_progress_label = '%d / %d' % (
                order.return_inspections_done,
                order.return_inspections_total,
            )

    @api.depends('inspection_ids', 'inspection_ids.state',
                 'order_line.product_id.categ_id', 'order_line.is_rental',
                 'has_inspection_required')
    def _compute_inspection_state_flags(self):
        for order in self:
            pickups = order.inspection_ids.filtered(
                lambda i: i.type == 'pickup' and i.state != 'cancelled'
            )
            returns = order.inspection_ids.filtered(
                lambda i: i.type == 'return' and i.state != 'cancelled'
            )

            all_pickups_done = bool(pickups) and all(i.state == 'done' for i in pickups)
            order.has_completed_pickups = all_pickups_done

            if not order.has_inspection_required:
                order.has_pending_pickup = False
            elif not pickups:
                order.has_pending_pickup = True
            else:
                order.has_pending_pickup = not all_pickups_done

            if not returns:
                order.has_pending_return = False
            else:
                order.has_pending_return = any(i.state != 'done' for i in returns)

    # -------------------------------------------------------------------------
    # Order confirmation — auto-create all inspection drafts
    # -------------------------------------------------------------------------

    def action_confirm(self):
        result = super().action_confirm()
        for order in self.filtered('is_rental_order'):
            order._auto_create_inspection_drafts()
        return result

    def _auto_create_inspection_drafts(self):
        """
        Called at order confirmation. Creates draft pickup AND return inspections
        for every qualifying rental line.

        The return inspection receives previous_inspection_id pointing directly
        to its pickup counterpart — set at creation, no lookup needed later.

        Safe to call multiple times — skips already-created unit numbers.
        """
        self.ensure_one()
        Inspection = self.env['rental.inspection']

        for line in self.order_line.filtered('is_rental'):
            product = line.product_id
            category = product.categ_id
            qty = int(line.product_uom_qty)

            has_pickup_template = bool(category.pickup_template_id)
            has_return_template = bool(category.return_template_id)

            if not has_pickup_template and not has_return_template:
                continue

            existing_pickup_units = set(
                self.inspection_ids.filtered(
                    lambda i: i.product_id == product
                    and i.type == 'pickup'
                    and i.state != 'cancelled'
                ).mapped('unit_number')
            )
            existing_return_units = set(
                self.inspection_ids.filtered(
                    lambda i: i.product_id == product
                    and i.type == 'return'
                    and i.state != 'cancelled'
                ).mapped('unit_number')
            )

            for unit_num in range(1, qty + 1):
                pickup_id = False

                if has_pickup_template and unit_num not in existing_pickup_units:
                    pickup = Inspection.create({
                        'rental_order_id': self.id,
                        'product_id': product.id,
                        'unit_number': unit_num,
                        'type': 'pickup',
                        'partner_id': self.partner_id.id,
                        'inspector_id': self.env.user.id,
                    })
                    pickup_id = pickup.id
                else:
                    # Pickup already exists — find it so we can link the return
                    existing = self.inspection_ids.filtered(
                        lambda i: i.product_id == product
                        and i.type == 'pickup'
                        and i.unit_number == unit_num
                        and i.state != 'cancelled'
                    )
                    if existing:
                        pickup_id = existing[0].id

                if has_return_template and unit_num not in existing_return_units:
                    Inspection.create({
                        'rental_order_id': self.id,
                        'product_id': product.id,
                        'unit_number': unit_num,
                        'type': 'return',
                        'partner_id': self.partner_id.id,
                        'inspector_id': self.env.user.id,
                        'previous_inspection_id': pickup_id,
                    })

    def _auto_create_serialised_pickup_inspections(self):
        """Sync pickedup_lot_ids → lot_id on inspections after pickup wizard validation."""
        self.ensure_one()
        for line in self.order_line.filtered('is_rental'):
            lots = line.pickedup_lot_ids
            if not lots:
                continue
            insp_for_line = self.inspection_ids.filtered(
                lambda i: i.product_id == line.product_id and i.state != 'cancelled'
            ).sorted('unit_number')
            sorted_lots = lots.sorted('id')
            for idx, insp in enumerate(insp_for_line):
                lot = sorted_lots[idx] if idx < len(sorted_lots) else sorted_lots[-1]
                if insp.lot_id != lot:
                    insp.lot_id = lot

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------

    def action_view_inspections(self):
        self.ensure_one()
        action = {
            'name': _('Inspections'),
            'type': 'ir.actions.act_window',
            'res_model': 'rental.inspection',
            'view_mode': 'list,form',
            'domain': [('rental_order_id', '=', self.id), ('state', '!=', 'cancelled')],
            'context': {'default_rental_order_id': self.id},
        }
        non_cancelled = self.inspection_ids.filtered(lambda i: i.state != 'cancelled')
        if len(non_cancelled) == 1:
            action.update({'view_mode': 'form', 'res_id': non_cancelled[0].id})
        return action

    def action_view_pickup_inspections(self):
        self.ensure_one()
        return {
            'name': _('Pickup Inspections'),
            'type': 'ir.actions.act_window',
            'res_model': 'rental.inspection',
            'view_mode': 'list,form',
            'domain': [
                ('rental_order_id', '=', self.id),
                ('type', '=', 'pickup'),
                ('state', '!=', 'cancelled'),
            ],
            'context': {'default_rental_order_id': self.id, 'default_type': 'pickup'},
        }

    def action_view_return_inspections(self):
        self.ensure_one()
        return {
            'name': _('Return Inspections'),
            'type': 'ir.actions.act_window',
            'res_model': 'rental.inspection',
            'view_mode': 'list,form',
            'domain': [
                ('rental_order_id', '=', self.id),
                ('type', '=', 'return'),
                ('state', '!=', 'cancelled'),
            ],
            'context': {'default_rental_order_id': self.id, 'default_type': 'return'},
        }
