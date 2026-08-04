COMMERCIAL_VIEW_XMLIDS = (
    "view_sale_order_form_southern_accounting_guardrails",
    "view_quotation_list_southern_accounting_guardrails",
    "view_purchase_order_form_southern_accounting_guardrails",
    "view_purchase_order_list_southern_accounting_guardrails",
)


def migrate(cr, version):
    """Retire blocking sales and purchase guardrails without removing accounting data."""
    cr.execute(
        """
        UPDATE southern_accounting_policy
           SET commercial_order_guardrail_mode = 'off',
               commercial_order_guardrail_effective_at = NULL
        """
    )
    cr.execute(
        """
        UPDATE ir_ui_view
           SET active = FALSE
         WHERE id IN (
             SELECT res_id
               FROM ir_model_data
              WHERE module = %s
                AND model = 'ir.ui.view'
                AND name IN %s
         )
        """,
        ("southern_accounting_guardrails", COMMERCIAL_VIEW_XMLIDS),
    )
