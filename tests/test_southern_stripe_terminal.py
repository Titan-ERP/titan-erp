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


def test_invoice_replaces_pay_with_three_explicit_payment_routes():
    view = (MODULE / "views" / "account_move_views.xml").read_text(encoding="utf-8")
    assert "//button[@id='account_invoice_payment_btn']" in view
    assert "//button[@id='account_invoice_payment_secondary_btn']" in view
    assert "move_type == 'out_invoice'" in view
    assert 'string="Pay with Terminal"' in view
    assert 'string="Pay with Cash"' in view
    assert 'string="Pay with ACH"' in view


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


def test_universal_processing_fee_is_configurable_and_disabled_until_account_mapping():
    route = (MODULE / "models" / "invoice_payment_route.py").read_text(encoding="utf-8")
    assert 'processing_fee_enabled = fields.Boolean(' in route
    assert 'default=False' in route
    assert 'default=3.5' in route
    assert 'default=0.30' in route
    assert 'processing_fee_income_account_id = fields.Many2one(' in route
    assert 'processing_fee_tax_ids = fields.Many2many(' in route


def test_processing_fee_applies_to_every_customer_invoice_and_is_idempotent():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    assert 'move.move_type != "out_invoice"' in source
    assert 'route.processing_fee_percentage / 100.0' in source
    assert 'route.processing_fee_fixed' in source
    assert 'move.amount_total - sum(fee_lines.mapped("price_total"))' in source
    assert 'fee_lines[:1]' in source
    assert 'len(fee_lines) > 1' in source
    assert '"southern_is_processing_fee": True' in source
    assert '"southern_manual_revenue_bucket": "fees"' in source


def test_processing_fee_is_finalized_before_posting_and_never_mutates_posted_invoice():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    assert 'move.state != "draft"' in source
    assert 'def action_post(self):' in source
    assert 'self._southern_sync_processing_fee(strict=True)' in source
    assert 'return super().action_post()' in source


def test_processing_fee_is_not_tied_to_terminal_cash_or_ach_route():
    source = (MODULE / "models" / "account_move.py").read_text(encoding="utf-8")
    sync_method = source.split("def _southern_sync_processing_fee", 1)[1].split("def create", 1)[0]
    assert "route_name" not in sync_method
    assert "provider_state" not in sync_method
