{
    "name": "LOKI CRM Parcel Link",
    "version": "19.0.1.0.0",
    "category": "Sales/CRM",
    "summary": "Auditable links between CRM records and external parcel records",
    "depends": ["crm"],
    "data": [
        "security/loki_crm_parcel_link_security.xml",
        "security/ir.model.access.csv",
        "views/loki_crm_parcel_link_views.xml",
        "views/crm_lead_views.xml",
        "views/loki_crm_parcel_link_menus.xml",
    ],
    "installable": True,
    "application": True,
    "license": "LGPL-3",
}
