{
    "name": "DMC Company Setup Wizard",
    "version": "19.0.1.0.0",
    "category": "Administration",
    "summary": "Multi-step wizard to automate new company accounting setup",
    "author": "DMC Strategic IT",
    "license": "LGPL-3",
    "depends": [
        "account",
        "payment",
        "base_setup",
    ],
    "data": [
        "security/ir.model.access.csv",
        "wizard/company_setup_wizard_views.xml",
    ],
    "installable": True,
    "application": True,
}
