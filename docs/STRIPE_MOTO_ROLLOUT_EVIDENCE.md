# Stripe Terminal MOTO Rollout Evidence

## Candidate

- PR: https://github.com/Titan-ERP/titan-erp/pull/124
- Branch: `codex/stripe-terminal-moto`
- Head: verify the latest PR head with `gh pr view 124 --repo Titan-ERP/titan-erp --json headRefOid`
- Module: `southern_stripe_terminal`
- Target version: `19.0.1.6.0`

## Accounting Decision

No blocking accounting flaw was found in the MOTO path. The change keeps payment capture separate from payout reconciliation:

- Invoice revenue remains on the original invoice lines.
- A supplemental processing-fee invoice is created only when the fee is not already embedded.
- One native `account.payment.register` payment settles the original invoice and fee invoice together.
- The configured Stripe incoming payment method clears to Outstanding Receipts.
- Payout reconciliation remains a separate accounting step.

## Southern Configuration To Verify Before Production Test

- Company: `Southern Equipment Company (Laurel)`
- Stripe provider: `Stripe`, enabled
- Terminal config: `SEC Laurel S710`
- Terminal journal: `Bank`
- Terminal incoming method: `Stripe`
- Incoming method clearing account: `Outstanding Receipts`
- Bank account: `Operating Checking - SEC Laurel`
- Stripe fee expense account: `Bank Merchant Fees`
- Processing fee income account: `Transaction Processing Fee Income`
- Processing fee tax: empty unless Southern confirms the fee is taxable
- `moto_enabled`: false until Stripe Support approval is documented
- User group: assign `Southern Payments / Stripe Pay by Phone` only to approved Pay-by-Phone users

## Validation Evidence

- `py -3.13 -m ruff check southern_stripe_terminal tests\test_southern_stripe_terminal.py`: passed
- `py -3.13 -m ruff check scripts\odoo_stripe_moto_rollout_preflight.py tests\test_southern_stripe_terminal.py`: passed
- `py -3.13 -m compileall southern_stripe_terminal`: passed
- `py -3.13 -m pytest tests\test_southern_stripe_terminal.py`: 22 passed
- `py -3.13 -m pytest tests`: 213 passed
- `git diff --check`: passed
- XML parse check for all `southern_stripe_terminal/**/*.xml`: passed
- Manifest data file existence check: passed
- GitHub validation checks on PR #124: passed on the pushed head used for this evidence update

## Read-Only Odoo Preflight

Run this before and after upgrade:

```powershell
py -3.13 scripts\odoo_stripe_moto_rollout_preflight.py --env C:\Users\cross\OneDrive\Documents\2.Titan\Odoo\odoo_connection.env
```

Expected before upgrade: `blocked` because production is still on `southern_stripe_terminal` `19.0.1.5.1`.

Expected after upgrade, before the supervised test: `pass` with `moto_enabled=false`.

During the approved supervised production test window only, rerun with:

```powershell
py -3.13 scripts\odoo_stripe_moto_rollout_preflight.py --env C:\Users\cross\OneDrive\Documents\2.Titan\Odoo\odoo_connection.env --allow-moto-enabled
```

## Production Gates

Do not merge or deploy until all gates are complete:

1. Odoo.sh test build for PR/head `2f26b57` is green or accepted with documented non-blocking warnings.
2. Labeled Odoo.sh production backup is created and recorded.
3. Rollback owner and smoke-test owner are named.
4. Stripe Support approval for MOTO on the Southern account is documented.
5. `moto_enabled` remains disabled until the supervised production test window.
6. Only approved users are assigned `Southern Payments / Stripe Pay by Phone`.

## Smallest Supervised Test

1. Use one low-value posted Southern customer invoice.
2. Confirm payment type is `Stripe Terminal - Pay by Phone`.
3. Enable `moto_enabled` only for the approved test window.
4. Start Pay by Phone from the invoice.
5. Enter card data only on the approved Stripe reader.
6. Confirm the Stripe PaymentIntent metadata includes the terminal payment id, company id, invoice amount, fee amount, and terminal mode.
7. Confirm the terminal payment record reaches `registered`.
8. Confirm `account_payment_id` exists.
9. Confirm the original invoice and supplemental fee invoice, if any, are settled by the same native Odoo payment.
10. After Stripe payout lands, reconcile payout net against Outstanding Receipts and Stripe fees without duplicate revenue or duplicate fee income.

## Blocked Items As Of 2026-08-10

- Odoo.sh test-build evidence is not yet captured in this task.
- Labeled Odoo.sh production backup evidence is not yet captured in this task.
- Stripe Support MOTO approval is not yet captured in this task.
- AWS CLI session is expired in this task, so no AWS deployment evidence was obtained.
