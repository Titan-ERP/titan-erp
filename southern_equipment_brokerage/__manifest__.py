{
    "name": "Southern Equipment Brokerage",
    "version": "19.0.1.18.0",
    "category": "Sales/CRM",
    "summary": "Broker-assisted sourced equipment listings, inquiries, inspections, and assignments.",
    "description": """
Southern Equipment's durable system of record for third-party sourced equipment
opportunities. The addon keeps source and seller data internal while publishing a
curated broker-assisted equipment catalog and accepting buyer inquiries.
""",
    "depends": [
        "contacts",
        "crm",
        "mail",
        "website",
    ],
    "data": [
        "security/southern_brokerage_security.xml",
        "security/ir.model.access.csv",
        "security/discovery_record_rules.xml",
        "data/southern_brokerage_sequences.xml",
        "views/equipment_discovery_views.xml",
        "views/equipment_listing_views.xml",
        "views/buyer_inquiry_views.xml",
        "views/brokered_deal_views.xml",
        "views/operations_views.xml",
        "views/equipment_comp_views.xml",
        "views/equipment_import_views.xml",
        "views/southern_brokerage_menus.xml",
        "views/website_listing_templates.xml",
    ],
    "assets": {
        "web.assets_frontend": [
            "southern_equipment_brokerage/static/src/css/brokerage.css",
        ],
    },
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
