{
    "name": "Southern Customer Portal",
    "version": "19.0.1.0.0",
    "category": "Website/Portal",
    "summary": "Customer portal pages for memberships, outstanding repair orders, and invoices.",
    "depends": [
        "portal",
        "account",
        "repair",
        "mail",
        "website_sale",
        "sale_subscription",
        "website_sale_subscription",
        "auth_signup",
    ],
    "data": [
        "security/ir.model.access.csv",
        "views/membership_views.xml",
        "views/customer_portal_templates.xml",
    ],
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
