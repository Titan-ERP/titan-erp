from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Stop universal fees and preserve existing terminal-payment audit records."""
    cr.execute(
        """
        UPDATE southern_stripe_terminal_payment
           SET invoice_amount = amount,
               processing_fee_amount = 0,
               processing_fee_embedded = FALSE
         WHERE invoice_amount IS NULL
            OR invoice_amount = 0
        """
    )
    env = api.Environment(cr, SUPERUSER_ID, {})
    draft_fee_lines = env["account.move.line"].search(
        [
            ("southern_is_processing_fee", "=", True),
            ("move_id.state", "=", "draft"),
        ]
    )
    draft_fee_lines.unlink()
