# Southern Accounting Guardrails Deployment

This module must be present on the Odoo server addon path before it can be installed from Apps or by `scripts/odoo_install_module.py`.

## Deploy

1. Copy `southern_accounting_guardrails` into the custom addons path used by the Odoo server or Odoo.sh project.
2. Update the app list.
3. Install `southern_accounting_guardrails`.
4. Upgrade to `19.0.1.11.0` so invoice, bank, and product work lanes recompute.
5. Open Accounting / Southern Accounting / Daily Controls and run `Refresh Counts`.

## First Review

- Confirm Revenue Rules include the rental equipment rules for TX60, TX18, TX10, and U35.
- Confirm Invoice Source Review shows only legacy or exception invoices, not every native Odoo invoice.
- Open Bank Blocked Exceptions and Merchant Settlements before ordinary matching.
- Open Revenue Bucket Review and resolve any lines where the detected bucket does not match the income account.
- Use Shop Boss Documents only for migration history and audit support while Shop Boss is phased out.
- Confirm the scheduled action `Southern Daily Accounting Control Refresh` is active.
