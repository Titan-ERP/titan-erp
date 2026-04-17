# -*- coding: utf-8 -*-
##########################################################################
# Author      : Webkul Software Pvt. Ltd. (<https://webkul.com/>)
# Copyright(c): 2015-Present Webkul Software Pvt. Ltd.
# All Rights Reserved.
#
# This program is copyright property of the author mentioned above.
# You can`t redistribute it and/or modify it.
#
# You should have received a copy of the License along with this program.
# If not, see <https://store.webkul.com/license.html/>
##########################################################################
{
    'name': 'Odoo Dealership Management',
    'summary': 'Dealership Management in Odoo for Business Expansion on another level',
    'description': '''
		Dealership is a method in which you authorize others to sell your products or
		services in a particular area or region. You can focus on the marketing of
		your brand and rest the dealers will do for you.
		Odoo Dealership Management facilitates you to manage dealerships and the
		related contracts from start to end of the process. The module has everything
		from registering dealers to an e-commerce for your customers who can find
		dealers in their region or the nearest location. keywords: franchise | franchise management
	''',
    'category': 'Website',
    'version': '1.0.0',
    'price':  299,
    'currency':  'USD',
    'author': 'Webkul Software Pvt. Ltd.',
    'license':  'Other proprietary',
    "live_test_url":  'http://odoodemo.webkul.com/?module=dealership_management',
    'website': 'https://store.webkul.com/Odoo-Dealership-Management.html',
    'depends': [
        'website',
        'website_crm',
        'portal_rating',
        'website_sale',
    ],
    'data': [
        'security/ir.model.access.csv',
        'views/application_history.xml',
        'views/dealership_application.xml',
        'views/dealership_plan.xml',
        'views/dealership_contract.xml',
        'views/application_faq.xml',
        'views/dealer_stock.xml',
        'views/res_config_setting.xml',
        'views/dashboard.xml',
        'views/menu.xml',
        'wizard/application_wizard.xml',
        'views/portal_templates.xml',
        'views/dashboard_templates.xml',
        'templates/mail.xml',
        'templates/report.xml',
        'data/website_data.xml',
    ],
    'assets': {
        "web.assets_frontend": [
            "https://fonts.googleapis.com/css?family=Montserrat",
            "/dealership_management/static/src/scss/portal.style.scss",
            "/dealership_management/static/src/scss/dashboard.style.scss",
            "/dealership_management/static/src/js/config.mixin.js",
            "/dealership_management/static/src/js/portal.main.js",
            "/dealership_management/static/src/js/dashboard.main.js",
            "/dealership_management/static/src/js/portal.faq.js",
            "/dealership_management/static/src/js/dealer.location.locator.js",
            'dealership_management/static/src/xml/faq_template.xml',
            'dealership_management/static/src/xml/locator_template.xml',
        ],
        "web.assets_backend": [
            '/web/static/lib/jquery/jquery.js',
            "/web/static/lib/Chart/Chart.js",
            "/dealership_management/static/src/scss/dashboard.backend.scss",
            "/dealership_management/static/src/js/config.mixin.js",
            'dealership_management/static/src/xml/backend_dashboard.xml',
            "/dealership_management/static/src/js/dashboard.backend.js",
        ],
    },
    'demo': [],
    'images': ['static/description/Banner.gif'],
    'application': True,
    'sequence': 1,
    'pre_init_hook': 'pre_init_check',
}
