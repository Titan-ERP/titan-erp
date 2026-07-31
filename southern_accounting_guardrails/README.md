# Southern Accounting Guardrails

Adds non-blocking accounting review fields and queues for Southern Equipment.

## What It Improves

- Adds Shop Boss source/reference fields to customer invoices and credit notes.
- Adds native Shop Boss document and payment-batch records for import, review, and coverage tracking.
- Adds native revenue bucket classification on invoice lines, including review flags when Rental, Service, or Parts revenue appears to be posted to the wrong income account.
- Adds a safe draft-only action to apply the expected income account to revenue lines before invoices are posted.
- Adds Revenue Rules so the team can keep classifications native in Odoo after Shop Boss is phased out.
- Adds product-level income account review so product/category setup errors can be fixed upstream.
- Adds Daily Controls with scheduled refresh counts for bank lines, reconciliation review, merchant batches, draft invoices, product setup issues, and revenue bucket exceptions.
- Adds accounting review status fields for invoices and bank statement lines.
- Adds manual bank-line bucket overrides and reviewed/exception controls without changing reconciliation.
- Flags merchant settlements, generic checks, and missing bank-line partners.
- Adds product/category revenue bucket fields for Parts, Service, Rental, Equipment, Fees, and Other.
- Adds Accounting menu views:
  - Southern Accounting / Daily Controls
  - Southern Accounting / Invoice Source Review
  - Southern Accounting / Revenue Bucket Review
  - Southern Accounting / Revenue Rules
  - Southern Accounting / Product Accounting Review
  - Southern Accounting / Shop Boss Documents
  - Southern Accounting / Shop Boss Payment Batches
  - Southern Accounting / Bank Matching Review

## What It Does Not Do

- It does not post, reconcile, unreconcile, or delete accounting entries.
- It does not block invoice posting.
- It does not replace the API cleanup scripts; it gives staff a native Odoo surface for the same review concepts.
- Shop Boss records are intended as migration history and audit support, not the long-term operating workflow.
