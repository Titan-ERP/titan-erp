# US Payroll Mississippi Tax Status

This Odoo addon adds Mississippi state filing statuses to the employee payroll tax setup and creates a Mississippi state income tax salary rule.

## Why this is an addon

`hr.employee.l10n_us_state_filing_status` is a base field from the United States payroll localization. Odoo blocks changing that selection list from Studio or Technical Settings, so Mississippi must be added through Python with `selection_add`.

## Employee statuses added

- `ms_single`: MS: Single
- `ms_head_of_family`: MS: Head-of-Family
- `ms_married_spouse_not_employed`: MS: Married (Spouse Not Employed)
- `ms_married_both_spouses_employed`: MS: Married (Both Spouses Employed)

## Withholding method

The `MSINCOMETAX` salary rule annualizes the pay-period taxable wage base, subtracts the filing-status standard deduction and the exact exemption amount claimed on Form 89-350, applies 4% tax to taxable income above $10,000, divides by the number of pay periods, adds any extra withholding, and rounds to the nearest whole dollar.

The amount on Form 89-350 Line 6 belongs in **State Withholding Allowance** exactly as
claimed. The addon does not add an automatic personal exemption. This also handles
`MS: Married (Both Spouses Employed)` correctly because each spouse enters only their
allocated share of the joint exemption. A blank amount calculates with zero exemption.

The implementation follows the Mississippi 2026 withholding formula published by the National Finance Center and the Mississippi Department of Revenue withholding table/instructions.

## Deployment checklist

1. Deploy this addon to the Odoo.sh custom addons repository.
2. Install or upgrade `US Payroll Mississippi Tax Status` in a staging database.
3. Confirm employee Payroll tab shows the four Mississippi statuses in State Tax Filing Status.
4. Generate test payslips for single, head-of-family, married spouse not employed, and married both spouses employed.
5. Compare at least one full payroll run against the current payroll provider or accountant-approved calculator before live employee payment.
6. Confirm the Southern Equipment salary-rule accounting preview matches the mapping below.

## Southern Equipment accounting map

The upgrade is intentionally scoped to `Southern Equipment Company (Laurel)`. It does not
change Titan, CROSS CAPITAL, or LOKI payroll mappings.

| Salary rule | Debit account | Credit account |
| --- | --- | --- |
| Gross Pay (`GROSS`) | `600000 Administrative Payroll` | Net balancing behavior |
| FIT, Social Security, Medicare | `210011 Employee Payroll Taxes Payable` | Net balancing behavior |
| Mississippi withholding (`MSINCOMETAX`) | `210013 Mississippi Withholding Payable` | Net balancing behavior |
| Employer Social Security, Medicare, FUTA, SUI | `600020 Employer Payroll Taxes` | `210012 Employer Payroll Taxes Payable` |
| Net Salary (`NET`) | Net balancing behavior | `210010 Accrued Payroll` |

Employee withholding rules produce negative payslip lines, so Odoo credits the configured
debit-side liability account. This follows the posting pattern of Odoo's US localization.
The migration creates only the three dedicated `210011`-`210013` liability accounts when
they are absent. Existing accounts must match the expected name, type, and Southern-only
company ownership or the upgrade stops without changing mappings.

## Assumptions to validate

- The salary rule uses Odoo's `TAXABLE` category as the Mississippi taxable wage base after pre-tax deductions.
- Odoo's state withholding allowance field is the total exemption claimed on Form 89-350 Line 6.
- The accounting map must be reviewed on a staging payslip before processing live payroll.
- `210010 Accrued Payroll`, `600000 Administrative Payroll`, and `600020 Employer Payroll Taxes`
  must remain the approved Southern Equipment accounts.
