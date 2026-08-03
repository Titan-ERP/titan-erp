CRON_XMLIDS = (
    "ir_cron_southern_bank_coding_candidates",
    "ir_cron_southern_daily_accounting_control",
)


def migrate(cr, version):
    """Keep supervised accounting jobs disabled after the noupdate transition."""
    cr.execute(
        """
        UPDATE ir_cron
           SET active = FALSE
         WHERE id IN (
             SELECT res_id
               FROM ir_model_data
              WHERE module = %s
                AND model = 'ir.cron'
                AND name IN %s
         )
        """,
        ("southern_accounting_guardrails", CRON_XMLIDS),
    )
    cr.execute(
        """
        UPDATE ir_model_data
           SET noupdate = FALSE
         WHERE module = %s
           AND model = 'ir.cron'
           AND name IN %s
        """,
        ("southern_accounting_guardrails", CRON_XMLIDS),
    )
