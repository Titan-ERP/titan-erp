def migrate(cr, version):
    del version
    # Older sweep startup logic demoted every accepted item to pending. Restore
    # those records so exact dealer-cost recovery can continue while the next
    # sweep is in progress. Completed reconciliation remains solely responsible
    # for marking unseen records stale.
    cr.execute(
        """
        UPDATE southern_sparex_discovery_item
           SET reconciliation_state = 'current'
         WHERE reconciliation_state = 'pending'
           AND state = 'verified'
           AND source_state = 'verified'
           AND odoo_match_state IN ('matched_active', 'missing')
           AND source_url IS NOT NULL
           AND source_url_sha256 IS NOT NULL
        """
    )
