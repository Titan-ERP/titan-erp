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
