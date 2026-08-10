import hashlib
import hmac
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "southern_stripe_terminal"


def _load_utils():
    spec = importlib.util.spec_from_file_location("southern_stripe_terminal_utils", MODULE / "utils.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_requires_native_accounting_and_stripe_modules():
    manifest = (MODULE / "__manifest__.py").read_text(encoding="utf-8")
    assert '"account"' in manifest
    assert '"payment_stripe"' in manifest
    assert '"southern_accounting_guardrails"' in manifest


def test_webhook_signature_validation_accepts_only_current_matching_signature():
    utils = _load_utils()
    payload = b'{"id":"evt_test","type":"payment_intent.succeeded"}'
    secret = "whsec_test"
    timestamp = 1_800_000_000
    digest = hmac.new(secret.encode(), str(timestamp).encode() + b"." + payload, hashlib.sha256).hexdigest()
    header = f"t={timestamp},v1={digest}"
    assert utils.verify_stripe_signature(payload, header, secret, now=timestamp)
    assert not utils.verify_stripe_signature(payload, header, "wrong", now=timestamp)
    assert not utils.verify_stripe_signature(payload, header, secret, now=timestamp + 301)


def test_terminal_mode_builders_keep_card_present_and_moto_requests_separate():
    utils = _load_utils()
    assert utils.stripe_terminal_payment_method_type("card_present") == "card_present"
    assert utils.stripe_terminal_process_data("pi_present", "card_present") == {
        "payment_intent": "pi_present"
    }
    assert utils.stripe_terminal_payment_method_type("moto") == "card"
    assert utils.stripe_terminal_process_data("pi_moto", "moto") == {
        "payment_intent": "pi_moto",
        "process_config[moto]": "true",
    }


def test_payment_flow_uses_native_registration_and_never_forces_invoice_paid():
    source = (MODULE / "models" / "stripe_terminal_payment.py").read_text(encoding="utf-8")
    assert 'self.env["account.payment.register"]' in source
    assert "._create_payments()" in source
    assert '"payment_state":' not in source
    assert "invoice.payment_state =" not in source


def test_terminal_webhook_uses_separate_signing_secret():
    provider_source = (MODULE / "models" / "payment_provider.py").read_text(encoding="utf-8")
    webhook_source = (MODULE / "controllers" / "webhook.py").read_text(encoding="utf-8")
    assert "stripe_terminal_webhook_secret" in provider_source
    assert "stripe_terminal_webhook_secret" in webhook_source


def test_write_capable_polling_cron_is_disabled_by_default():
    cron = (MODULE / "data" / "ir_cron.xml").read_text(encoding="utf-8")
    assert '<field name="active" eval="False"/>' in cron


def test_invoice_replaces_pay_with_four_explicit_payment_routes():
    view = (MODULE / "views" / "account_move_views.xml").read_text(encoding="utf-8")
    assert "//button[@id='account_invoice_payment_btn']" in view
    assert "//button[@id='account_invoice_payment_secondary_btn']" in view
    assert "move_type == 'out_invoice'" in view
    assert 'string="Pay with Terminal"' in view
    assert 'string="Pay by Phone"' in view
    assert 'groups="southern_stripe_terminal.group_stripe_terminal_moto"' in view
    assert 'string="Pay with Cash"' in view
    assert 'string="Pay with ACH"' in view
    assert 'name="southern_payment_type"' in view
    assert 'readonly="state != \'draft\'"' in view


def test_cash_and_ach_reuse_native_payment_registration():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    assert "def action_pay_with_cash(self):" in source
    assert "def action_pay_with_ach(self):" in source
    assert "self.action_register_payment()" in source
    assert '"default_journal_id"' in source
    assert '"default_payment_method_line_id"' in source


def test_odoo_19_views_and_constraints_use_current_syntax():
    payment_view = (MODULE / "views" / "stripe_terminal_payment_views.xml").read_text(encoding="utf-8")
    assert "<search>" in payment_view
    assert '<group string="Group By">' not in payment_view
    for path in (MODULE / "models").glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert "_sql_constraints" not in source


def test_terminal_processing_fee_is_configurable_and_disabled_until_account_mapping():
    route = (MODULE / "models" / "invoice_payment_route.py").read_text(encoding="utf-8")
    assert 'processing_fee_enabled = fields.Boolean(' in route
    assert 'default=False' in route
    assert 'default=3.5' in route
    assert 'default=0.30' in route
    assert 'processing_fee_income_account_id = fields.Many2one(' in route
    assert 'processing_fee_tax_ids = fields.Many2many(' in route


def test_processing_fee_is_snapshotted_only_by_terminal_action():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    assert "def _southern_terminal_fee_snapshot(self):" in source
    assert "fee_snapshot = self._southern_terminal_fee_snapshot()" in source
    assert "self.amount_residual * route.processing_fee_percentage / 100.0" in source
    assert 'route.processing_fee_fixed' in source
    assert 'def action_pay_with_cash(self):' in source
    assert 'def action_pay_with_ach(self):' in source
    assert "_southern_terminal_fee_snapshot" not in source.split(
        "def action_pay_with_cash(self):", 1
    )[1].split("def action_pay_with_ach(self):", 1)[0]


def test_manual_payment_type_controls_the_draft_fee_line():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    assert "southern_payment_type = fields.Selection(" in source
    assert '("stripe_terminal", "Stripe Terminal")' in source
    assert '("stripe_moto", "Stripe Terminal - Pay by Phone")' in source
    assert '("cash", "Cash")' in source
    assert '("ach", "ACH")' in source
    assert '("online_link", "Online Payment Link")' in source
    assert "def _southern_sync_terminal_fee_line" in source
    assert "if move.southern_terminal_fee_payment_id:" in source
    assert 'move.southern_payment_type not in ("stripe_terminal", "stripe_moto")' in source
    assert "fee_lines.with_context(southern_skip_processing_fee_sync=True).unlink()" in source
    assert '"southern_is_processing_fee": True' in source
    assert "def action_update_processing_fee" not in source
    assert "def action_post(self):" in source
    assert "self._southern_sync_terminal_fee_line(strict=True)" in source
    assert "@api.model_create_multi" in source


def test_new_customer_invoices_default_to_an_active_company_terminal():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    assert "def _default_southern_payment_type(self):" in source
    assert 'self.env.context.get("default_move_type") != "out_invoice"' in source
    assert '("active", "=", True)' in source
    assert '("is_default", "=", True)' in source
    assert 'return "stripe_terminal" if has_default_terminal else False' in source
    assert 'vals["southern_payment_type"] = "stripe_terminal"' in source


def test_upgrade_retires_only_the_legacy_studio_fee_path_and_migrates_drafts():
    migration = (
        MODULE / "migrations" / "19.0.1.5.0" / "post-migrate.py"
    ).read_text(encoding="utf-8")
    assert "studio_customization.credit_card_processi_" in migration
    assert "automation.active = False" in migration
    assert 'LEGACY_PAYMENT_FIELD = "x_studio_customer_payment_method"' in migration
    assert 'node.set("invisible", "True")' in migration
    assert '("state", "=", "draft")' in migration
    assert '("move_type", "=", "out_invoice")' in migration
    assert 'line.product_id.default_code == "CARD-FEE"' in migration
    assert '"Credit Card": "stripe_terminal"' in migration
    assert '"ACH": "ach"' in migration
    assert '"Check / Cash": "cash"' in migration
    assert "button_draft" not in migration
    assert "action_post" not in migration


def test_payment_buttons_honor_the_manual_payment_type():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    assert "def _southern_validate_selected_payment_type(self, payment_type):" in source
    assert "self._southern_validate_selected_payment_type(payment_type)" in source
    assert 'self._southern_validate_selected_payment_type(route_name)' in source


def test_moto_flow_is_explicit_permissioned_and_reuses_native_reconciliation():
    account_move = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    payment = (MODULE / "models" / "stripe_terminal_payment.py").read_text(encoding="utf-8")
    config = (MODULE / "models" / "stripe_terminal_config.py").read_text(encoding="utf-8")
    groups = (MODULE / "security" / "groups.xml").read_text(encoding="utf-8")

    assert "def action_pay_by_phone(self):" in account_move
    assert 'payment_type="stripe_moto"' in account_move
    assert 'payment_mode="moto"' in account_move
    assert '("payment_mode", "=", payment_mode)' in account_move
    assert 'config_domain.append(("moto_enabled", "=", True))' in account_move
    assert "group_stripe_terminal_moto" in account_move

    assert 'payment_mode = fields.Selection(' in payment
    assert "A Stripe Terminal payment mode cannot be changed after creation." in payment
    assert 'payment.payment_mode == "moto" and not payment.config_id.moto_enabled' in payment
    assert 'stripe_terminal_payment_method_type(self.payment_mode)' in payment
    assert 'stripe_terminal_process_data(self.payment_intent_id, self.payment_mode)' in payment
    assert '"metadata[odoo_terminal_mode]": self.payment_mode' in payment
    assert "def _require_moto_access(self):" in payment
    assert 'self.env["account.payment.register"]' in payment

    assert "moto_enabled = fields.Boolean(" in config
    assert 'id="group_stripe_terminal_moto"' in groups
    assert "account.group_account_invoice" in groups
    assert 'id="account.group_account_manager"' not in groups
    assert 'id="base.group_system"' not in groups


def test_terminal_fee_uses_linked_supplemental_invoice_and_one_native_payment():
    source = (MODULE / "models" / "stripe_terminal_payment.py").read_text(encoding="utf-8")
    assert "def _ensure_processing_fee_invoice(self):" in source
    assert '"southern_terminal_fee_payment_id": self.id' in source
    assert '"southern_is_processing_fee": True' in source
    assert '"southern_manual_revenue_bucket": "fees"' in source
    assert "fee_invoice.action_post()" in source
    assert "invoices = invoice | fee_invoice" in source
    assert 'active_ids=invoices.ids' in source


def test_terminal_payment_snapshots_fee_and_prevents_double_charge():
    source = (MODULE / "models" / "stripe_terminal_payment.py").read_text(encoding="utf-8")
    assert "processing_fee_embedded = fields.Boolean(" in source
    assert "processing_fee_percentage = fields.Float(" in source
    assert "processing_fee_income_account_id = fields.Many2one(" in source
    assert "if self.processing_fee_embedded or self.currency_id.is_zero(" in source
    assert "UNIQUE(southern_terminal_fee_payment_id)" in (
        MODULE / "models" / "account_move.py"
    ).read_text(encoding="utf-8")


def test_global_currency_is_not_subject_to_odoo_company_domain_checks():
    source = (MODULE / "models" / "stripe_terminal_payment.py").read_text(encoding="utf-8")
    assert 'currency_id = fields.Many2one("res.currency", required=True)' in source
    assert 'currency_id = fields.Many2one("res.currency", required=True, check_company=True)' not in source
    assert "payment.currency_id != payment.invoice_id.currency_id" in source
    assert "payment.currency_id != payment.config_id.currency_id" in source


def test_invoice_users_can_read_only_their_company_terminal_configuration():
    access = (MODULE / "security" / "ir.model.access.csv").read_text(encoding="utf-8")
    rules = (MODULE / "security" / "record_rules.xml").read_text(encoding="utf-8")
    assert (
        "access_stripe_terminal_config_invoice,stripe.terminal.config invoice,"
        "model_southern_stripe_terminal_config,account.group_account_invoice,1,0,0,0"
    ) in access
    assert "<record id=\"stripe_terminal_config_company_rule\"" in rules
    assert "ref('account.group_account_invoice')" in rules
    assert "[('company_id', 'in', company_ids)]" in rules


def test_upgrade_removes_only_draft_universal_fee_lines():
    migration = (
        MODULE / "migrations" / "19.0.1.3.0" / "post-migrate.py"
    ).read_text(encoding="utf-8")
    assert '("move_id.state", "=", "draft")' in migration
    assert "draft_fee_lines.unlink()" in migration
    assert "button_draft" not in migration
    assert "posted" not in migration
