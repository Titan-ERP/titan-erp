# Southern Stripe Terminal

This Odoo 19 module sends the exact residual balance of a posted customer invoice to a Stripe Terminal reader and registers the successful charge through Odoo's native `account.payment.register` workflow. It keeps card-present and telephone-order (MOTO) payments as separate, immutable payment modes.

Posted customer invoices replace the generic **Pay** action with four explicit choices:

- **Pay with Terminal** collects an in-person card-present payment.
- **Pay by Phone** collects a Stripe-approved MOTO payment by entering card details only on a supported reader.
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
- Requires a dedicated Odoo access group and an administrator-confirmed reader setting before MOTO can start.
- Creates MOTO PaymentIntents with `card` and sends `process_config[moto]=true`; ordinary Terminal payments remain `card_present`.
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

## Pay by Phone (MOTO)

MOTO is available only on Stripe Reader S700/S710 and BBPOS WisePOS E devices and must first be enabled by Stripe Support. After approval:

1. An Accounting Administrator enables **MOTO Enabled by Stripe** on the default reader.
2. An administrator assigns **Southern Payments / Stripe Pay by Phone** only to authorized employees.
3. The employee opens a posted customer invoice and clicks **Pay by Phone**.
4. Card number, expiration, CVC, and postal code are entered on the reader, never in Odoo.
5. The existing signed webhook and idempotent reconciliation path create the native Odoo payment after Stripe reports success.

MOTO is card-not-present. Card-present pricing, fraud liability shift, and other card-present protections do not apply. Use it only when a customer who is not physically present initiates the payment by phone or mail.

## Accounting behavior

The configured Odoo journal and incoming payment method control payment accounting. Depending on the journal's outstanding-receipts configuration, Odoo may show the invoice as **In Payment** until the Stripe payout/bank line is reconciled. The module intentionally does not force the invoice to **Paid**, because doing so would bypass Odoo's accounting controls.

## Transaction processing fee

The invoice has one authoritative **Payment Type** selection. New customer invoices default to **Stripe Terminal** when the company has an active default reader, and automatically receive one itemized processing-fee line. Choosing Cash, ACH, or Online Payment Link removes that draft fee line. The default formula is 3.5% of the complete invoice total before the fee, including sales tax, plus $0.30.

The upgrade disables the obsolete Studio card-fee automation and hides its duplicate payment-type field. Existing draft invoices are translated to the authoritative selection; posted invoices and their accounting history are not rewritten.

The line is recalculated whenever draft invoice lines change and finalized before posting. Once posted, Payment Type and accounting lines remain immutable. The Terminal button reuses the embedded fee and does not charge it twice. For a legacy posted invoice that did not receive a fee line before posting, the terminal workflow retains the linked supplemental-fee fallback so the posted journal entry is never silently rewritten.

## Hardware cutover

When the physical S710 arrives:

1. Create/verify the live Stripe Location.
2. Register the S710 to the live Stripe account and location.
3. Configure a live Odoo reader record using the returned `tmr_...` ID.
4. Test reader connectivity and webhook signatures.
5. Run one controlled low-value invoice payment and reconcile its Stripe payout.
6. Only then make the live reader the default.
