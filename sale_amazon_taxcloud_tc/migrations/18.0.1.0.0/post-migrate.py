import logging


def migrate(cr, version):
    if not version:
        return
    logger = logging.getLogger(__name__)

    cr.execute(
        """update ir_ui_view
               set active=True
               where
               name like 'res.config.settings.view.form.inherit.sale.amazon.taxcloud'
               and arch_fs like '%sale_amazon_taxcloud_tc%'
               and active=False
               returning id"""
    )
    updated_row_ids = cr.dictfetchall()
    logger.info(str(updated_row_ids))
    logger.info(len(updated_row_ids))
    logger.info(
        "Activated the 'res.config.settings.view.form.inherit.account.taxcloud' view"
    )
