def migrate(cr, version):
    """Move native Odoo invoices out of Invoice Source Review."""
    cr.execute(
        """
        UPDATE account_move
           SET southern_review_status = 'not_required'
         WHERE southern_review_status = 'needs_review'
           AND move_type IN ('out_invoice', 'out_refund')
           AND COALESCE(southern_source_system, 'odoo') <> 'shop_boss'
           AND COALESCE(southern_shop_boss_verified, FALSE) IS NOT TRUE
           AND COALESCE(southern_has_shop_boss_reference, FALSE) IS NOT TRUE
           AND (southern_shop_boss_number IS NULL OR southern_shop_boss_number = '')
        """
    )
