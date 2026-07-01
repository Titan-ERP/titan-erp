# -*- coding: utf-8 -*-
{
    'name': "DMC Sales Updates",

    'summary': "Adds Shop Supply's charge option to sale orders",

    'author': "DMC Strategict It",
    'website': "https://www.dmcstrategicit.com",

    'version': '19.0.1.0.0',

    'application': False,
    'installable': True,

    'license': 'OPL-1',

    'depends': ['sale_management'],

    'data': [
        'views/sale_order_views.xml',
        'views/account_move_views.xml',
        'report/sale_order_report.xml',
    ],

    'assets': {
        'web.assets_backend': [
            'dmc_sales_updates/static/src/components/tax_totals_patch.xml',
        ],
    },
}
