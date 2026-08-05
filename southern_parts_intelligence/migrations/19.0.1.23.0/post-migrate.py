def migrate(cr, version):
    cr.execute(
        """
        UPDATE website
           SET prevent_zero_price_sale = TRUE
         WHERE prevent_zero_price_sale IS DISTINCT FROM TRUE
        """
    )
