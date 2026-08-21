def migrate(cr, version):
    del version
    cr.execute(
        """
        UPDATE southern_vendor_catalog_item
           SET media_state = COALESCE(NULLIF(media_state, ''), 'pending'),
               media_attempt_count = COALESCE(media_attempt_count, 0)
         WHERE media_state IS NULL
            OR media_attempt_count IS NULL
        """
    )
