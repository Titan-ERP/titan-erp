# Southern Accounting Guardrails

Adds non-blocking accounting review fields and queues for Southern Equipment.

## Southern Accounting Automation

This module now contains the first Odoo-native accounting automation control
plane. The first autonomous lane is Bank Coding only:

`Bank transaction -> deterministic Odoo rule -> candidate -> optional AI enrichment -> Odoo policy gate -> apply or review`

AI output is advisory only. It never authorizes accounting changes. Autonomous
accounting writes require an independently satisfied deterministic Odoo rule and
an Odoo policy decision immediately before the write.

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
- Adds Southern Accounting Automation policies, runs, findings, candidate
  authorization fields, evaluation fingerprints, policy versioning, rollout
  modes, structured reason codes, and guarded apply controls.
- Adds an explicit guarded bank-coding apply method with idempotency keys,
  company-isolation checks, daily absolute-value caps, and service-account ACLs.
- Adds Accounting menu views:
  - Southern Accounting / Daily Controls
  - Southern Accounting / Automation Policies
  - Southern Accounting / Automation Runs
  - Southern Accounting / Automation Findings
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
- It does not allow AI confidence to authorize accounting changes.
- It does not create bills, invoices, journal entries, payments, or broad
  reconciliation matches.
- AWS/service users should use the automation worker group and call the guarded
  candidate method; they should not be granted generic journal-line write access.
