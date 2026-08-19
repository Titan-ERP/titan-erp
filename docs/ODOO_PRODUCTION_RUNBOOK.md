# Odoo production hardening runbook

## Release scope

This release consolidates the Odoo automation control plane, CRM/contact data
quality, Product Master Quality queue, daily Operations Control dashboard, and
bounded multi-company scheduled actions. Shop Boss is explicitly out of scope
and must remain retired.

Modules upgraded:

| Module | Version |
| --- | --- |
| `southern_parts_intelligence` | `19.0.1.2.0` |
| `southern_customer_portal` | `19.0.1.0.16` |
| `southern_equipment_brokerage` | `19.0.1.19.0` |
| `southern_operations_control` | `19.0.1.1.0` |

The public partner-pricing application is disabled by default through
`southern_customer_portal.partner_application_enabled=false`.

## Required pre-deployment gate

1. Merge only a reviewed pull request targeting `main-production`.
2. Require the GitHub validation workflow and a successful Odoo.sh test build.
3. Pause every product/catalog/AWS/archive worker after its current command
   returns. Confirm no command is running or waiting to apply.
4. Confirm `aws sts get-caller-identity` reports a named least-privilege role,
   not the AWS account root identity.
5. Create a labeled manual Odoo.sh production backup and record its timestamp,
   database, revision, restore availability, release operator, and rollback
   operator.
6. Confirm the database has enough capacity and every worker volume reports at
   least 2 GB free.
7. Keep `ODOO_WRITE_ENABLED` unset throughout module deployment.

## Odoo.sh test-build validation

Upgrade the modules in this order:

1. `southern_parts_intelligence`
2. `southern_customer_portal`
3. `southern_equipment_brokerage`
4. `southern_operations_control`

Stop on a registry failure, traceback, invalid view, missing model, access
error, constraint failure, or unexpected data mutation. Confirm all affected
models and views load, then run the read-only smoke tests below.

## Scheduled-action state

- Product catalog internal maintenance is disabled per sync configuration until
  `Internal Cron Enabled` is explicitly reviewed and enabled.
- Product Master Quality is active daily. It uses a per-company cursor, scans at
  most 500 products per company per invocation, and prevents overlapping runs.
- Evidence fetch is bounded and overlap-protected. It permits HTTPS only,
  validates redirect destinations, blocks non-public addresses, limits response
  size, and may be further restricted by the
  `southern_parts_intelligence.evidence_allowed_hosts` system parameter.
- Equipment comp analysis, portal order review, and Operations Control are
  bounded, overlap-protected, and company-scoped.
- No scheduled action publishes products, changes live prices, approves
  accounting entries, or applies contact imports.

## Read-only smoke tests

1. Open **Southern Operations → Daily Control** for each allowed company and
   verify the counts differ when the underlying company data differs.
2. Open **Product Master Quality** and confirm work lanes split live website
   fixes, unpublished enrichment, and ready-to-publish. Ready products must
   not appear in the default Needs Work filter or Daily Control open-issue
   count. Review blocker, evidence, taxonomy, and duplicate categories.
3. Open **Product Automation Runs** and verify idempotency key, worker command,
   artifact, hash, schema, archive URI, and archive verification fields.
4. Run CRM classification preview; confirm imported references remain separate
   from actual opportunities and provenance is visible.
5. Upload a small contact sample and prepare matches. Confirm exact,
   ambiguous-review, new-candidate, and skipped rows without creating partners.
6. Confirm public `/partner-application` returns not found and the account page
   no longer advertises partner pricing.
7. Verify existing sales, accounting, inventory, website, equipment, and
   service workflows still open.

## First supervised product batch

Do not start this batch during module deployment.

1. Approve exactly one catalog workflow in Odoo and confirm no other run is
   active for its company/mode.
2. Produce a dry-run artifact first.
3. Confirm the proposed record count is within the approved bound and the worker
   reports at least 2 GB free.
4. Archive the versioned artifact to its approved S3 prefix and verify SHA-256
   metadata.
5. Open a short write window with `ODOO_WRITE_ENABLED=true`, an exact workflow
   confirmation value, and a recorded business reason.
6. Run one batch. Record one idempotency key and one worker command ID.
7. Finish the Odoo ledger entry only after the local artifact URI, SHA-256,
   schema version, S3 archive URI, and archive verification are present.
8. Close the write window immediately. Review Odoo changes before authorizing
   another batch.

## Rollback

Stop new scheduled and external runs immediately on registry failure,
unexpected write behavior, website publication changes, company-data leakage,
or access regression.

1. Capture the Odoo.sh build, traceback, command ID, ledger record, and artifact.
2. Restore the labeled pre-deployment database backup.
3. Revert the production merge through Git; do not rewrite branch history.
4. Redeploy the prior known-good revision.
5. Verify Accounting, Sales, Inventory, Website, CRM, Equipment, and Service
   before reopening automation.

Git rollback alone is insufficient after a module upgrade because database
schema and data changes may already exist.
