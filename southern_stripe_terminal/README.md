# Southern Stripe Terminal

This Odoo 19 module sends the exact residual balance of a posted customer invoice to a Stripe Terminal reader and registers the successful card-present charge through Odoo's native `account.payment.register` workflow.

Posted customer invoices replace the generic **Pay** action with three explicit choices:

- **Pay with Terminal** sends the exact residual to Stripe Terminal.
- **Pay with Cash** opens native payment registration with the configured cash journal and method.
- **Pay with ACH** opens native payment registration with the configured ACH bank journal and method.

Cash and ACH require operator review and confirmation in Odoo's standard payment wizard. The ACH route records a payment through the configured Odoo method; it does not initiate an external bank debit by itself.

## Safety properties

- Reuses Odoo's configured Stripe provider and API credentials.
- Uses a dedicated signed Stripe Terminal webhook endpoint.
- Uses stable idempotency keys for PaymentIntent and reader actions.
- Never writes invoice totals, invoice lines, taxes, journal entries, or payment status directly.
- Registers the accounting payment only after retrieving a `succeeded` PaymentIntent from Stripe.
- Stops in `Needs Review` if the Odoo invoice balance changes after Stripe collected the card.
- Stores Stripe and Odoo identifiers, but no PAN, CVV, customer card data, or API secrets in transaction records.
- Ships the polling cron disabled; signed webhooks and the operator Refresh Status action are available immediately.

## Test before hardware arrives

1. Configure Odoo's Stripe provider in **Test Mode**.
2. Create a test Stripe Terminal Location in Stripe.
3. Create a reader configuration in Odoo with that Location ID, the Stripe test provider, a bank/cash journal, and its incoming payment method.
4. Save the configuration and click **Create Simulated S710**.
5. Click **Configure Terminal Webhook** once the Odoo database has a stable public HTTPS URL.
6. Activate and mark the reader as the company default.
7. Open a posted test customer invoice and click **Pay with Stripe Terminal**.
8. On the terminal payment, click **Simulate Card**.
9. Confirm one Stripe PaymentIntent, one Odoo payment, correct reconciliation, and no duplicate payment after webhook replay or repeated refresh.

Stripe's simulator is sandbox-only and does not move money. Live mode must remain disabled until the physical reader is registered, the correct location is verified, and a controlled low-value end-to-end test passes.

## Accounting behavior

The configured Odoo journal and incoming payment method control payment accounting. Depending on the journal's outstanding-receipts configuration, Odoo may show the invoice as **In Payment** until the Stripe payout/bank line is reconciled. The module intentionally does not force the invoice to **Paid**, because doing so would bypass Odoo's accounting controls.

## Transaction processing fee

The company payment-route configuration applies the fee only when an employee clicks **Pay with Terminal**. Cash, ACH, ordinary invoices, and online payment links do not receive it. The default formula is 3.5% of the outstanding invoice balance, including sales tax, plus $0.30.

The terminal payment snapshots the percentage, fixed amount, income account, and tax selection before sending the total to Stripe. After Stripe succeeds, Odoo creates and posts one linked fee-only invoice, then registers one native payment across the original invoice and the fee invoice. This keeps the posted customer invoice immutable while giving the fee a separate revenue line and an idempotent accounting trail. If a legacy posted invoice already contains a processing-fee line, the terminal reuses that amount and does not charge a second fee.

## Hardware cutover

When the physical S710 arrives:

1. Create/verify the live Stripe Location.
2. Register the S710 to the live Stripe account and location.
3. Configure a live Odoo reader record using the returned `tmr_...` ID.
4. Test reader connectivity and webhook signatures.
5. Run one controlled low-value invoice payment and reconcile its Stripe payout.
6. Only then make the live reader the default.
