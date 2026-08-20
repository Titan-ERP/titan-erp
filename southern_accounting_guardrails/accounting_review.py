"""Standalone Southern accounting review classification.

These helpers stay independent of Odoo so operator-queue rules can be tested
without the registry. The Odoo models store the resulting work lane and
details sentence.
"""

INCOME_ACCOUNT_PREFIXES = {
    "parts": "410",
    "service": "420",
    "rental": "430",
    "equipment": "44",
}
COST_ACCOUNT_PREFIXES = {
    "equipment": "500",
    "parts": "510",
    "service": "520",
    "rental": "530",
}
RESERVED_OPERATING_INCOME_PREFIXES = ("410", "420", "430")
CROSS_POSTED_REVENUE_BUCKETS = frozenset({"freight", "fees"})


def join_review_details(reasons):
    if not reasons:
        return False
    if len(reasons) == 1:
        return reasons[0][0].upper() + reasons[0][1:] + "."
    return "; ".join(reasons).capitalize() + "."


def invoice_default_review_status(
    source_system,
    has_shop_boss_reference=False,
    shop_boss_number=None,
):
    """Native Odoo invoices do not start in Invoice Source Review."""
    source_system = source_system or "odoo"
    if source_system == "shop_boss" or has_shop_boss_reference or shop_boss_number:
        return "needs_review"
    return "not_required"


def classify_invoice_review(
    source_system,
    review_status,
    shop_boss_verified=False,
    has_shop_boss_reference=False,
    shop_boss_number=None,
):
    """Return ``(lane, details)`` for a customer invoice source review."""
    source_system = source_system or "odoo"
    if review_status == "exception":
        return "exception", "Marked as an invoice source exception."
    if review_status == "verified" or shop_boss_verified:
        return "verified", "Invoice source is verified."
    has_legacy = (
        source_system == "shop_boss"
        or bool(has_shop_boss_reference)
        or bool(shop_boss_number)
    )
    if has_legacy:
        return (
            "source_review",
            "Legacy Shop Boss source still needs verification.",
        )
    if review_status == "needs_review":
        return "needs_review", "Invoice remains in the generic review queue."
    return "not_required", "Native Odoo invoice with no legacy source to verify."


def classify_bank_review(
    review_status,
    *,
    is_reconciled=False,
    is_merchant_settlement=False,
    settlement_direct_revenue=False,
    payroll_direct_expense=False,
    review_bucket=None,
    is_generic_check=False,
    missing_partner=False,
):
    """Return ``(lane, details)`` for a bank matching review line."""
    if review_status == "reviewed":
        return "reviewed", "Bank line already reviewed."
    if review_status == "not_required":
        return "not_required", "No Southern bank review is required."
    if review_status == "exception" or payroll_direct_expense or settlement_direct_revenue:
        reasons = []
        if settlement_direct_revenue:
            reasons.append("merchant settlement is coded directly to revenue")
        if payroll_direct_expense:
            reasons.append("payroll withdrawal is coded directly to expense")
        if review_status == "exception" and not reasons:
            reasons.append("marked as a bank matching exception")
        return "blocked", join_review_details(reasons)
    if is_merchant_settlement and not is_reconciled:
        return (
            "merchant",
            "Merchant settlement must be matched to the processor clearing batch.",
        )
    if review_bucket == "payroll" and not is_reconciled:
        return (
            "payroll",
            "Payroll withdrawal must be reconciled to the posted payroll liability.",
        )
    if is_generic_check or review_bucket == "check_payee":
        return "check_payee", "Generic check is missing a payee partner."
    if missing_partner:
        return "missing_partner", "Bank line is missing a partner."
    return "ordinary", "Ordinary bank line still needs matching review."


def classify_product_accounting_review(
    sale_ok,
    revenue_bucket,
    income_review,
    expense_review,
    *,
    require_product_bucket=True,
    expected_income_name=None,
    expected_expense_name=None,
):
    """Return ``(lane, details)`` for a saleable product accounting review."""
    if not sale_ok:
        return "ok", False
    reasons = []
    missing_bucket = bool(require_product_bucket and not revenue_bucket)
    if missing_bucket:
        reasons.append("saleable product has no Southern revenue bucket")
    if income_review == "needs_review" and not missing_bucket:
        if expected_income_name:
            reasons.append(f"income account should be {expected_income_name}")
        else:
            reasons.append("income account does not match the expected revenue bucket")
    if expense_review == "needs_review":
        if expected_expense_name:
            reasons.append(f"cost account should be {expected_expense_name}")
        else:
            reasons.append("cost account does not match the expected cost bucket")

    has_income = income_review == "needs_review" or missing_bucket
    has_cost = expense_review == "needs_review"
    if not reasons:
        return "ok", False
    details = join_review_details(reasons)
    if missing_bucket and has_cost:
        return "both", details
    if missing_bucket:
        return "missing_bucket", details
    if has_income and has_cost:
        return "both", details
    if has_income:
        return "income", details
    return "cost", details


def classify_revenue_line_review(
    is_customer_revenue_line,
    bucket,
    account_code,
    *,
    override=None,
    manual_note=None,
    expected_account_name=None,
    current_account_name=None,
    expected_account_mismatch=False,
):
    """Return ``(review, details)`` for a customer income line."""
    if override == "accepted":
        return "ok", manual_note or "Accepted by accounting review."
    if override == "exception":
        return "exception", manual_note or "Marked as accounting exception."
    if not is_customer_revenue_line:
        return "ok", False
    if expected_account_mismatch:
        expected = expected_account_name or "the configured income account"
        current = current_account_name or account_code or "the current account"
        return "needs_review", f"Expected {expected}; currently {current}."
    prefix = INCOME_ACCOUNT_PREFIXES.get(bucket)
    code = account_code or ""
    if prefix and code and not code.startswith(prefix):
        return (
            "needs_review",
            f"Expected {bucket} revenue account; currently {code or current_account_name}.",
        )
    if bucket in CROSS_POSTED_REVENUE_BUCKETS and code.startswith(RESERVED_OPERATING_INCOME_PREFIXES):
        return (
            "needs_review",
            f"{bucket.capitalize()} revenue is posted to an operating income account ({code}).",
        )
    if bucket == "other":
        return "needs_review", "Revenue bucket could not be classified natively."
    return "ok", False


def product_income_needs_review(
    sale_ok,
    bucket,
    account_code,
    *,
    require_product_bucket=True,
    expected_mismatch=False,
    has_account=True,
):
    if sale_ok and require_product_bucket and not bucket:
        return True
    if expected_mismatch and has_account:
        return True
    prefix = INCOME_ACCOUNT_PREFIXES.get(bucket)
    code = account_code or ""
    if prefix and has_account and code and not code.startswith(prefix):
        return True
    return bool(
        bucket in CROSS_POSTED_REVENUE_BUCKETS
        and code.startswith(RESERVED_OPERATING_INCOME_PREFIXES)
    )


def product_expense_needs_review(bucket, account_code, *, expected_mismatch=False, has_account=True):
    prefix = COST_ACCOUNT_PREFIXES.get(bucket)
    if not prefix:
        return "not_required"
    if not has_account:
        return "needs_review"
    if expected_mismatch:
        return "needs_review"
    code = account_code or ""
    if code and not code.startswith(prefix):
        return "needs_review"
    return "ok"
