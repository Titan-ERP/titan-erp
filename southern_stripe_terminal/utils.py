import hashlib
import hmac
import time

TERMINAL_PAYMENT_MODE_CARD_PRESENT = "card_present"
TERMINAL_PAYMENT_MODE_MOTO = "moto"


def stripe_terminal_payment_method_type(payment_mode):
    """Return the Stripe PaymentIntent method for an immutable terminal mode."""
    if payment_mode == TERMINAL_PAYMENT_MODE_CARD_PRESENT:
        return "card_present"
    if payment_mode == TERMINAL_PAYMENT_MODE_MOTO:
        return "card"
    raise ValueError(f"Unsupported Stripe Terminal payment mode: {payment_mode}")


def stripe_terminal_process_data(payment_intent_id, payment_mode):
    """Build reader processing data without ever handling cardholder data."""
    data = {"payment_intent": payment_intent_id}
    if payment_mode == TERMINAL_PAYMENT_MODE_MOTO:
        data["process_config[moto]"] = "true"
    elif payment_mode != TERMINAL_PAYMENT_MODE_CARD_PRESENT:
        raise ValueError(f"Unsupported Stripe Terminal payment mode: {payment_mode}")
    return data


def verify_stripe_signature(payload, signature_header, secret, *, tolerance=300, now=None):
    """Verify Stripe's timestamped v1 webhook signature without logging secrets or payloads."""
    if not payload or not signature_header or not secret:
        return False
    values = {}
    for item in signature_header.split(","):
        key, separator, value = item.partition("=")
        if separator:
            values.setdefault(key.strip(), []).append(value.strip())
    try:
        timestamp = int(values.get("t", [""])[0])
    except ValueError:
        return False
    current_time = int(time.time() if now is None else now)
    if abs(current_time - timestamp) > tolerance:
        return False
    signed_payload = str(timestamp).encode() + b"." + payload
    expected = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in values.get("v1", []))
