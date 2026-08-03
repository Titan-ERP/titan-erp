def migrate(cr, version):
    cr.execute(
        """
        UPDATE southern_parts_catalog_sync AS sync
           SET name = 'Sparex Product Update Orchestrator',
               mode = 'sparex_discovery',
               state = 'idle',
               internal_cron_enabled = FALSE,
               approval_state = 'not_required',
               approved_by_id = NULL,
               approved_at = NULL,
               last_message = 'Upgraded to Odoo-owned dispatch; scheduling remains disabled pending approval.'
          FROM ir_model_data AS data
         WHERE data.module = 'southern_parts_intelligence'
           AND data.name = 'southern_parts_catalog_sync_sparex_updates'
           AND data.model = 'southern.parts.catalog.sync'
           AND sync.id = data.res_id
        """
    )
    cr.execute(
        """
        UPDATE southern_parts_catalog_sync AS sync
           SET state = 'paused',
               internal_cron_enabled = FALSE,
               last_message = 'Legacy JSON-2 datetime failure cleared; scheduling remains disabled.'
          FROM ir_model_data AS data
         WHERE data.module = 'southern_parts_intelligence'
           AND data.name = 'southern_parts_catalog_sync_snapshot_refresh'
           AND data.model = 'southern.parts.catalog.sync'
           AND sync.id = data.res_id
           AND sync.state = 'error'
           AND sync.last_message LIKE %s
        """,
        ["%does not match format '%Y-%m-%d %H:%M:%S'%"],
    )
