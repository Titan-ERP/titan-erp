# Southern Accounting System

First-stop map of how Titan ERP reviews, classifies, and approves accounting
work. Odoo is the system of record. Shop Boss is retired history only.

This module does not post, reconcile, unreconcile, or delete accounting
entries by itself. Bank coding apply is the one supervised write path, and it
still requires a manager approval plus a valid Bank Suspense Account line.

## Daily starting point

Open **Accounting → Southern Accounting → Daily Controls**, then Refresh
Counts.

| Lane | What it counts | Open this next |
| --- | --- | --- |
| Bank work | Today's statement lines still in an open matching lane | Daily Controls → Bank Work |
| Blocked bank exceptions | Direct-to-revenue merchant settlements and payroll coded to expense | Bank Blocked Exceptions |
| Merchant settlements | Card/net deposits that still need a processor clearing match | Merchant Settlements |
| Pending bank coding | Approved-rule candidates waiting for review or apply | Bank Coding Candidates |
| Invoice source review | Legacy Shop Boss invoices that still need verification | Invoice Source Review |
| Revenue bucket review | Income lines on the wrong account or still classified as Other | Revenue Bucket Review |
| Product accounting | Saleable products with a missing bucket or wrong income/cost account | Product Accounting Review |

Today's activity counts stay on the same form: that day's bank lines, draft
invoices, and revenue exceptions. Open-work-lane counts are the backlog, not
just today.

## Operator queues

### Bank matching

Bank Matching Review no longer dumps every statement line into one list.
Default work is everything that is not already reviewed.

- **Blocked Exception**: unsafe coding. Do not mark reviewed until the line
  uses payroll liabilities or payment clearing.
- **Merchant Settlement**: match the deposit to Outstanding Receipts / the
  processor batch. Fees are a separate expense line.
- **Payroll**: reconcile to the posted payroll liability.
- **Check Payee / Missing Partner**: identify the payee before coding.
- **Ordinary Review**: remaining unmatched lines.

Each row stores a details sentence that says why it is in that lane.

### Bank coding

Approved rules create candidates. They do not change accounts.
The daily candidate cron evaluates Southern Equipment companies only, matching
Daily Accounting Control.

1. Review **Bank Coding Rules** and approve only rules with a real target
   account.
2. Let the daily candidate cron or a manual run evaluate unmatched lines.
3. Approve a candidate, then Apply Coding.
4. Apply is rejected when the merchant target is unsafe, the line is already
   reconciled, or the move does not have exactly one Bank Suspense Account
   line.

### Invoice source review

Native Odoo customer invoices start as **Not Required**. They do not belong
in this queue.

Work needed means:

- a Shop Boss source or extracted Shop Boss reference still needs
  verification; or
- a reviewer marked a generic review or an exception.

### Revenue buckets

Invoice income lines are classified from product, category, Revenue Rules,
then a conservative text/account-code guess. A mismatch against Accounting
Policies, a leftover **Other** bucket, or freight/fee income posted to
410/420/430 is needs-review. Daily Controls count the open backlog, not just
today's invoices.

Fill From Chart also looks up **Transaction Processing Fee Income** (the
live Stripe fee account) before the older Card Processing Fee Income name.

Apply Account is draft-only. Posted invoices stay visible for review and
override; they are not rewritten.

### Product accounting

`Require Product Revenue Bucket` is enforced. A saleable product without a
Southern revenue bucket is a **Missing Revenue Bucket** row, not a silent OK.

Income and cost account reviews still compare the product/category account
to the company Accounting Policy and expected 410/420/430 and 500/510/520/530
prefixes.

## What accounting does not do

- It does not replace Odoo bank reconciliation.
- It does not auto-apply bank coding.
- It does not block invoice posting.
- Shop Boss Documents and Payment Batches are audit history, not the daily
  operating workflow.
- Standard-cost and inventory valuation changes remain a separate accounting
  workflow from Sparex supplier-cost publication.

## Upgrade

Upgrade `southern_accounting_guardrails` to `19.0.1.11.0`. The post-migrate
moves native Odoo invoices that were sitting in `needs_review` to
`not_required` when they have no Shop Boss source or reference.
