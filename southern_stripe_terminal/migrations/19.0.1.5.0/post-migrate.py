from lxml import etree
from odoo import SUPERUSER_ID, api

LEGACY_AUTOMATION_XMLID = (
    "studio_customization.credit_card_processi_856c5ec0-05f8-4d18-9a95-81936f645fab"
)
LEGACY_PAYMENT_FIELD = "x_studio_customer_payment_method"
LEGACY_PAYMENT_MAP = {
    "Credit Card": "stripe_terminal",
    "ACH": "ach",
    "Check / Cash": "cash",
}


def _disable_legacy_fee_automation(env):
    automation = env.ref(LEGACY_AUTOMATION_XMLID, raise_if_not_found=False)
    if automation and automation._name == "base.automation":
        automation.active = False


def _hide_legacy_payment_field(env):
    studio_views = env["ir.ui.view"].search(
        [
            ("model", "=", "account.move"),
            ("arch_db", "ilike", LEGACY_PAYMENT_FIELD),
        ]
    )
    for view in studio_views:
        root = etree.fromstring(view.arch_db.encode())
        changed = False
        for node in root.xpath(f".//field[@name='{LEGACY_PAYMENT_FIELD}']"):
            if node.get("invisible") != "True":
                node.set("invisible", "True")
                changed = True
        if changed:
            view.arch_db = etree.tostring(root, encoding="unicode")


def _migrate_draft_invoice_payment_types(env):
    AccountMove = env["account.move"]
    if LEGACY_PAYMENT_FIELD not in AccountMove._fields:
        return

    terminal_company_ids = (
        env["southern.stripe.terminal.config"]
        .search([("active", "=", True), ("is_default", "=", True)])
        .mapped("company_id")
        .ids
    )
    if not terminal_company_ids:
        return

    drafts = AccountMove.search(
        [
            ("company_id", "in", terminal_company_ids),
            ("move_type", "=", "out_invoice"),
            ("state", "=", "draft"),
        ]
    )
    legacy_fee_lines = drafts.invoice_line_ids.filtered(
        lambda line: line.product_id.default_code == "CARD-FEE"
    )
    legacy_fee_lines.with_context(skip_card_fee_automation=True).unlink()

    for invoice in drafts:
        legacy_payment_type = invoice[LEGACY_PAYMENT_FIELD]
        payment_type = LEGACY_PAYMENT_MAP.get(legacy_payment_type, "stripe_terminal")
        invoice.with_context(skip_card_fee_automation=True).write(
            {
                LEGACY_PAYMENT_FIELD: False,
                "southern_payment_type": payment_type,
            }
        )


def migrate(cr, version):
    """Retire the duplicate Studio fee path and default Southern drafts to Terminal."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    _disable_legacy_fee_automation(env)
    _hide_legacy_payment_field(env)
    _migrate_draft_invoice_payment_types(env)
