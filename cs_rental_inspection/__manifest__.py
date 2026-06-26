# -*- coding: utf-8 -*-
{
    'name': 'Rental Equipment Inspections',
    'version': '19.0.1.0.11',
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
    """,
    'author': 'Cyder Solutions',
    'website': 'https://www.cyder.com.au',
    'license': 'OPL-1',
    'depends': [
        'sale_renting',
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
