import re


MERCHANT_SETTLEMENT_RE = re.compile(
    r"BANKCARD|MERCHANT|MTOT DEP|NET SETTLE",
    re.IGNORECASE,
)
UNSAFE_MERCHANT_TARGET_NAMES = {
    "Accounts Receivable",
    "Bank Suspense Account",
    "Equipment Sales Revenue",
    "Parts Revenue",
    "Rental Revenue",
    "Sales Tax Payable",
    "Service Revenue",
}


def is_unsafe_merchant_target(bank_line, account):
    """Return whether a receipt would bypass payment clearing unsafely."""
    label = " ".join(
        value
        for value in (
            bank_line.payment_ref,
            getattr(bank_line, "ref", False),
            getattr(bank_line, "partner_name", False),
        )
        if value
    )
    if bank_line.amount <= 0 or not MERCHANT_SETTLEMENT_RE.search(label):
        return False
    return account.name in UNSAFE_MERCHANT_TARGET_NAMES or account.account_type == "income"
