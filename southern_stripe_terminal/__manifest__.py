# ruff: noqa: B018

{
    "name": "Southern Stripe Terminal",
    "version": "19.0.1.5.0",
    "category": "Accounting/Payment",
    "summary": "Collect invoice balances on Stripe Terminal and register native Odoo payments.",
    "author": "Southern Equipment Company",
    "website": "https://github.com/Titan-ERP/titan-erp",
    "depends": [
        "account",
        "mail",
        "payment_stripe",
        "southern_accounting_guardrails",
    ],
    "data": [
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "data/ir_cron.xml",
        "views/stripe_terminal_config_views.xml",
        "views/invoice_payment_route_views.xml",
        "views/stripe_terminal_payment_views.xml",
        "views/account_move_views.xml",
        "views/stripe_terminal_menus.xml",
    ],
    "external_dependencies": {"python": ["requests"]},
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
