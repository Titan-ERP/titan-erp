import hashlib
import hmac
import time


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
