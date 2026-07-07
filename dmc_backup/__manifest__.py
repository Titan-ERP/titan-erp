# -*- coding: utf-8 -*-
{
    'name': "DMC Backup",

    'summary': "Scheduled database backup to Azure Blob Storage or OneDrive with in-app history",

    'description': """
Scheduled database backup stored in Odoo filestore with in-app history list and
retention-based cleanup. Supports Azure Blob Storage and OneDrive (Microsoft
Graph API) as backup destinations.

Features
--------
- Scheduled daily backup via cron (configurable)
- Backup history list with size, state, and download link
- Multiple backup destinations (Azure Blob Storage, OneDrive)
- Configurable retention period per destination
- OneDrive folder browser to select the target folder interactively

""",

    'author': "DMC Strategic IT",
    'website': "https://www.dmcstrategicit.com",

    'version': '19.0.15.0.0',

    'application': True,
    'installable': True,

    'license': 'LGPL-3',

    'external_dependencies': {'python': ['requests']},

    'depends': ['base'],

    'data': [
        'security/ir.model.access.csv',
        'views/dmc_backup_log_views.xml',
        'views/dmc_backup_config_views.xml',
        'views/dmc_backup_folder_wizard_views.xml',
        'data/dmc_backup_cron.xml',
    ],
}
