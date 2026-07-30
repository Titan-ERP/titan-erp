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

The `MSINCOMETAX` salary rule annualizes the pay-period taxable wage base, subtracts the filing-status standard deduction and personal exemption, applies 4% tax to taxable income above $10,000, divides by the number of pay periods, adds any extra withholding, and rounds to the nearest whole dollar.

The implementation follows the Mississippi 2026 withholding formula published by the National Finance Center and the Mississippi Department of Revenue withholding table/instructions.

## Deployment checklist

1. Deploy this addon to the Odoo.sh custom addons repository.
2. Install or upgrade `US Payroll Mississippi Tax Status` in a staging database.
3. Confirm employee Payroll tab shows the four Mississippi statuses in State Tax Filing Status.
4. Generate test payslips for single, head-of-family, married spouse not employed, and married both spouses employed.
5. Compare at least one full payroll run against the current payroll provider or accountant-approved calculator before live employee payment.
6. Confirm the `MSINCOMETAX` salary rule posts to the expected payroll withholding liability account.

## Assumptions to validate

- The salary rule uses Odoo's `TAXABLE` category as the Mississippi taxable wage base after pre-tax deductions.
- Odoo's state withholding allowance field is treated as an additional exemption amount beyond the status-based exemption.
- Account code `230100` is the correct payroll withholding liability account for Mississippi state income tax.
