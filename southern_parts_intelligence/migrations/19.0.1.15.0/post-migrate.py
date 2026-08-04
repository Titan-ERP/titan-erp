def migrate(cr, version):
    cr.execute(
        """
        UPDATE ir_cron AS cron
           SET active = TRUE,
               interval_number = 1,
               interval_type = 'minutes'
          FROM ir_model_data AS data
         WHERE data.module = 'southern_parts_intelligence'
           AND data.name = 'ir_cron_southern_parts_catalog_sync'
           AND data.model = 'ir.cron'
           AND cron.id = data.res_id
        """
    )
