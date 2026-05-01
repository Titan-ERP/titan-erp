# -*- coding: utf-8 -*-
{
    'name': 'Titan Theme',
    'version': '19.0.2.0.0',
    'category': 'Theme/Corporate',
    'summary': 'Premium Tesla-inspired theme for TITAN Equipment — white page, dark hero, product cards.',
    'description': """
        Custom website theme for TITAN Equipment.
        - White page background (Tesla-style) — photography drives the visual
        - Cinematic full-screen hero with dark image overlay
        - Inter Tight headings (500 weight), Inter body
        - Tesla-pattern product cards: full-bleed image, text bottom-left, sharp corners
        - Light grey (#F4F4F4) stats band and CTA sections
        - Dark footer (#1B1319)
    """,
    'author': 'Titan Equipment',
    'website': 'https://titan-equip-staging-main-41626-31029343.dev.odoo.com',
    'depends': ['website'],
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
