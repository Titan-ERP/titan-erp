import re

MERCHANT_RE = re.compile(r"BANKCARD|MERCHANT|MTOT DEP|NET SETTLE", re.IGNORECASE)
CHECK_RE = re.compile(r"\bCHECK\b|INTUIT.*/CHECKS", re.IGNORECASE)


def classify_review_bucket(
    payment_ref,
    amount,
    *,
    has_partner=False,
    supplier_rank=0,
    manual_bucket=None,
):
    ref = payment_ref or ""
    if manual_bucket:
        return manual_bucket
    if MERCHANT_RE.search(ref) and amount > 0:
        return "merchant_fee"
    if CHECK_RE.search(ref) and not has_partner:
        return "check_payee"
    upper_ref = ref.upper()
    if "LOAN" in upper_ref or " LN " in upper_ref:
        return "loan"
    if "IRS" in upper_ref or "TAX" in upper_ref:
        return "tax"
    if "PAYROLL" in upper_ref or "INTUIT" in upper_ref:
        return "payroll"
    if amount > 0 and supplier_rank:
        return "vendor"
    if amount > 0:
        return "shop_boss_payment"
    return "vendor"


def payroll_direct_expense_risk(payment_ref, amount, account_types):
    ref = (payment_ref or "").upper()
    is_payroll = amount < 0 and ("PAYROLL" in ref or "INTUIT" in ref)
    return is_payroll and bool({"expense", "expense_direct_cost"} & set(account_types))


def settlement_direct_revenue_risk(payment_ref, amount, account_types):
    is_settlement = amount > 0 and bool(MERCHANT_RE.search(payment_ref or ""))
    return is_settlement and bool({"income", "income_other"} & set(account_types))
