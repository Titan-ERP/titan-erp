# ruff: noqa: B018

{
    "name": "LOKI Internal Website",
    "version": "19.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Authenticated internal dashboard for LOKI CRM and parcel work",
    "depends": [
        "website",
        "crm",
        "mail",
        "loki_crm_parcel_link",
    ],
    "data": [
        "views/loki_internal_templates.xml",
        "views/loki_internal_menus.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "loki_internal_website/static/src/css/loki_internal_website.css",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
