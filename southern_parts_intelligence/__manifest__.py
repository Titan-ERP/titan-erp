{
    "name": "Southern Parts Intelligence",
    "version": "19.0.1.0.2",
    "category": "Inventory/Inventory",
    "summary": "Adds OEM cross references, fitment, specifications, catalog pages, and source tracking to parts.",
    "author": "Southern Equipment Company",
    "depends": [
        "product",
        "stock",
        "purchase",
        "website_sale",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/catalog_sync_cron.xml",
        "views/product_template_views.xml",
        "views/parts_intelligence_views.xml",
        "views/catalog_sync_views.xml",
        "views/website_product_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "southern_parts_intelligence/static/src/scss/parts_catalog.scss",
        ],
    },
    "installable": True,
    "application": False,
    "license": "LGPL-3",
}
