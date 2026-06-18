# -*- coding: utf-8 -*-
{
    'name': 'Rental Equipment Inspections',
    'version': '19.0.1.0.6',
    'category': 'Inventory/Rental',
    'summary': 'Equipment condition inspections for rental pickups and returns',
    'description': """
Rental Equipment Inspection Management
===============================================
* Create and manage equipment inspection templates
* Record equipment condition at pickup and return
* Track equipment issues and damage
* Digital signature capture
* Photo attachments for condition records

Changelog
---------
19.0.1.0.6
  - Fixed: reserved_lot_ids dropdown now correctly filters to RENTL/Stock;
    previous version used exact match on complete_name which fails when the
    full path includes parent locations (e.g. Physical Locations/RENTL/Stock);
    switched to ilike and added view-level XPath on rental_order_primary_form_view
    to ensure the domain is applied to the inline list widget

19.0.1.0.5
  - Changed: reserved_lot_ids on rental order lines is now filtered to only
    show lots with stock in RENTL/Stock location; prevents assigning lots
    from other warehouses or locations during reservation
  - Changed: sale_stock_renting added as explicit dependency

19.0.1.0.4
  - Changed: at order confirmation, the reserved lot (reserved_lot_ids) is now
    automatically assigned to all pickup and return inspection records for the
    line; previously only pickup inspections were synced and only when
    pickedup_lot_ids was already populated
  - Changed: lot assignment now applies to both pickup and return inspections
    for a line; all units belonging to the same lot receive the same serial
    number, matching Odoo's lot-tracked product behaviour
  - Changed: post-confirmation lot sync is now triggered when reserved_lot_ids
    changes in addition to pickedup_lot_ids, keeping inspection serial numbers
    in step with reservation updates
  - Fixed: lot_id on rental.inspection now declares readonly=False, allowing
    the field to be written programmatically and edited in the form view
    """,
    'author': 'Cyder Solutions',
    'website': 'https://www.cyder.com.au',
    'license': 'OPL-1',
    'depends': [
        'sale_renting',
        'sale_stock_renting',
        'product',
        'mail',
    ],
    'data': [
        'security/inspection_security.xml',
        'security/ir.model.access.csv',
        'data/inspection_sequence.xml',
        'data/inspection_templates_data.xml',
        'reports/inspection_report_view.xml',
        'views/rental_inspection_views.xml',
        'views/rental_inspection_template_views.xml',
        'views/product_views.xml',
        'views/sale_views.xml',
        'views/menu_views.xml',
    ],
    'demo': [
        #'data/inspection_demo.xml',
    ],
    'application': True,
    'installable': True,
    'auto_install': False,
    'price': 99.99,
    'currency': 'USD',
    'sequence': -50,
    'images': ['static/description/icon.png'],
}
