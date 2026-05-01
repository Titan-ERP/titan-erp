# -*- coding: utf-8 -*-
{
    'name': 'Titan Theme',
    'version': '19.0.1.0.0',
    'category': 'Theme/Corporate',
    'summary': 'Premium dark theme for TITAN Equipment website — Tesla-inspired landing page.',
    'description': """
        Custom website theme for TITAN Equipment.
        - Cinematic full-screen hero with dark overlay
        - Inter Tight headings, Inter body
        - Dark monochrome palette (#1B1319 background, white text)
        - Product card sections
        - Minimal, premium spacing
    """,
    'author': 'Titan Equipment',
    'website': 'https://titan-equip-staging-main-41626-31029343.dev.odoo.com',
    'depends': ['website', 'web_editor'],
    'data': [
        'views/assets.xml',
        'views/snippets/snippets.xml',
    ],
    'assets': {
        # Primary variables must load before Bootstrap so they override defaults
        'web._assets_primary_variables': [
            'titan_theme/static/src/scss/primary_variables.scss',
        ],
        # Bootstrap overrides load after Bootstrap variables but before compilation
        'web.assets_frontend': [
            'titan_theme/static/src/scss/bootstrap_overrides.scss',
            'titan_theme/static/src/scss/titan_theme.scss',
        ],
    },
    'installable': True,
    'application': False,
    'auto_install': False,
    'license': 'LGPL-3',
}
