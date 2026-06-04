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

Changelog
---------
19.0.4.0.0
  - Added OneDrive (Microsoft Graph API) as a second backup destination
  - Added storage_type selection on backup configuration
  - Added Client Credentials OAuth2 flow for unattended OneDrive uploads
  - Added resumable chunked upload (10 MB chunks) for large backup files
  - Added interactive OneDrive folder browser wizard
  - Added Test Connection button for OneDrive destinations
  - Renamed azure_url field to storage_url on backup log

19.0.3.0.0
  - Added configurable retention_days per destination record
  - Fixed status badge rendering in backup destinations list

19.0.2.0.0
  - Added Azure Blob Storage push via SAS token
  - Added backup log with storage URL column

19.0.1.0.0
  - Initial release: scheduled SQL dump, zip packaging, in-app history
""",

    'author': "DMC Strategic IT",
    'website': "https://www.dmcstrategicit.com",

    'version': '19.0.4.0.0',

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
