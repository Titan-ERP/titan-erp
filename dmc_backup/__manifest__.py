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
19.0.7.0.0
  - Fixed: replaced custom SQL dump generator with pg_dump subprocess so backup
    files restore correctly on any Odoo SH or self-hosted environment; the
    custom generator failed on CREATE SCHEMA when the PostgreSQL role lacked
    CREATE privilege, causing a full transaction rollback and empty database
  - Changed: neutralization SQL is now appended after the pg_dump output in its
    own BEGIN/COMMIT block rather than embedded inside the dump transaction

19.0.6.0.0
  - Fixed: _delete_remote_files now routes by the per-record storage_type field
    so changing the default config does not silently skip blob deletion
  - Fixed: action_download routes by stored storage_type instead of URL pattern
  - Fixed: OneDrive deletion uses the actual uploaded filename (capturing
    post-rename name from the Graph API response) instead of the original
    requested filename
  - Fixed: enum type query now excludes extension-owned types (pg_depend filter)
    to prevent CREATE TYPE conflicts on restore
  - Fixed: trigger query now excludes extension-owned triggers for same reason
  - Fixed: setval uses double-quoted sequence identifiers to handle mixed-case
    sequence names from custom modules
  - Fixed: retention cleanup wrapped in try/except so a filestore or DB error
    during cleanup cannot roll back the success log write
  - Changed: dump.sql is now wrapped in BEGIN/COMMIT for atomic restore —
    either the entire restore succeeds or the database is left empty (previously
    removed in 19.0.5.0.0; re-added because partial restores are harder to
    diagnose than a clean failure)

19.0.5.0.0
  - Added neutralize option: deactivates crons, mail servers, CDN and removes
    sensitive API keys when restoring to a non-production environment
  - Added include_filestore option: allows database-only dumps without filestore
  - Added triggers and check constraints to dump for full schema fidelity
  - Added custom enum types to dump (extension-owned types excluded)
  - Fixed double-semicolon on view definitions produced by pg_views.definition
  - Added view dependency ordering to guarantee correct CREATE VIEW sequencing
  - Added extensions (pg_trgm, unaccent, vector), schemas, and user-defined
    functions to the dump so GIN/trgm/vector indexes restore correctly

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

    'version': '19.0.7.0.0',

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
