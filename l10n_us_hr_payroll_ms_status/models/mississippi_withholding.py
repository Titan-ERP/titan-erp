from decimal import Decimal, ROUND_HALF_UP


MS_FILING_STATUSES = {
    "ms_single": {
        "label": "MS: Single",
        "standard_deduction": Decimal("2300"),
    },
    "ms_head_of_family": {
        "label": "MS: Head-of-Family",
        "standard_deduction": Decimal("3400"),
    },
    "ms_married_spouse_not_employed": {
        "label": "MS: Married (Spouse Not Employed)",
        "standard_deduction": Decimal("4600"),
    },
    "ms_married_both_spouses_employed": {
        "label": "MS: Married (Both Spouses Employed)",
        "standard_deduction": Decimal("2300"),
    },
}

MS_PAY_PERIODS = {
    "annually": Decimal("1"),
    "annual": Decimal("1"),
    "semi-annually": Decimal("2"),
    "quarterly": Decimal("4"),
    "bi-monthly": Decimal("6"),
    "monthly": Decimal("12"),
    "semi-monthly": Decimal("24"),
    "semimonthly": Decimal("24"),
    "bi-weekly": Decimal("26"),
    "biweekly": Decimal("26"),
    "weekly": Decimal("52"),
    "daily": Decimal("260"),
}


def with_ms_filing_statuses(selection):
    values = list(selection or [])
    existing = {value for value, _label in values}
    for value, config in MS_FILING_STATUSES.items():
        if value not in existing:
            values.append((value, config["label"]))
    return values


def _money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def calculate_ms_withholding(
    pay_period_taxable_wages,
    filing_status,
    pay_periods_per_year,
    exemption_claimed=0,
    extra_withholding=0,
):
    status = MS_FILING_STATUSES.get(filing_status)
    if not status:
        return Decimal("0.00")

    periods = Decimal(str(pay_periods_per_year or "0"))
    if periods <= 0:
        return Decimal("0.00")

    annualized_wages = _money(pay_period_taxable_wages) * periods
    taxable_income = (
        annualized_wages
        - status["standard_deduction"]
        - _money(exemption_claimed)
    )
    if taxable_income <= Decimal("10000"):
        period_tax = Decimal("0")
    else:
        annual_tax = (taxable_income - Decimal("10000")) * Decimal("0.04")
        period_tax = annual_tax / periods

    period_tax += _money(extra_withholding)
    if period_tax <= 0:
        return Decimal("0.00")
    return period_tax.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
