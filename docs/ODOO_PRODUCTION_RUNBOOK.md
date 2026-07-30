# Odoo Native Control Plane Production Runbook

## Release Candidate

- Pull request: `Titan-ERP/titan-erp#14`
- Target branch: `main-production`
- Candidate branch: `agent/odoo-native-control-deploy`
- Validated code commit: `eefee408fe5982e8499fe2feb065738504bf7636`
- Known-good production parent: `6e996cd737728f4a49c2788acf33d8f785bf9b2c`
- Odoo.sh development build: `35667969`
- Odoo version: `19.0`

The candidate is four commits ahead of `main-production` with no production-only
commits missing from its history. Odoo.sh loaded the registry successfully with
all 265 modules. The final install and runtime logs contain no `WARNING`,
`ERROR`, or `CRITICAL` entries related to this release.

The Odoo.sh build card reports `Test: Warning`, but the build completed, exposes
`CONNECT`, loaded the complete registry, and passed the release smoke tests
below. Treat the badge as a documented platform-level exception; do not ignore
any new registry, module, or traceback warning during the production build.

## Scope

Existing modules upgraded:

| Module | Production version | Candidate version |
| --- | --- | --- |
| `southern_parts_intelligence` | `19.0.1.0.15` | `19.0.1.1.0` |
| `southern_accounting_guardrails` | `19.0.1.4.3` | `19.0.1.5.0` |
| `southern_equipment_brokerage` | `19.0.1.17.6` | `19.0.1.18.0` |

New module installed last:

- `southern_operations_control` `19.0.1.0.0`

Shop Boss remains historical audit data only. It is not a dependency of the
new Operations Control module and must not be reactivated.

## Pre-deployment Gate

Complete every item before merging:

1. Confirm pull request #14 still targets `main-production`, remains mergeable,
   contains validated code commit
   `eefee408fe5982e8499fe2feb065738504bf7636`, and has no later source changes.
2. Confirm the deployment branch still has `main-production` commit
   `6e996cd737728f4a49c2788acf33d8f785bf9b2c` as its merge base.
3. Pause product/catalog/AWS/archive workers after their current command returns.
   Confirm no product automation command is running or waiting to apply changes.
4. Create a labeled manual Odoo.sh production backup immediately before merge:
   `Pre-deployment backup: Odoo native control plane eefee40`.
5. Record the backup timestamp, database, revision, and restore availability.
   Do not rely only on an older automatic backup.
6. Announce a maintenance window and identify the release operator and rollback
   operator.
7. Confirm the latest local validation:
   `py -3 -m unittest discover -s tests -v`.

## Deployment

1. Merge pull request #14 into `main-production`.
2. Watch the Odoo.sh production build until the registry is loaded and the
   production endpoint is healthy. Stop on any new traceback, registry failure,
   missing model, invalid view, or access-control error.
3. Confirm the Apps list sees all four module versions.
4. Upgrade or verify the installed modules in this order:
   1. `southern_parts_intelligence`
   2. `southern_accounting_guardrails`
   3. `southern_equipment_brokerage`
   4. install `southern_operations_control`
5. Do not run imports, accounting applies, discovery conversions, archive
   operations, or product publication changes during the module upgrade.

## Scheduled Action Safety

Verify these exact states after module installation:

- `Southern Product Master Quality Refresh`: inactive. Keep inactive until the
  initial queue has been reviewed manually.
- `Southern Bank Coding Candidate Refresh`: active; it creates review candidates
  only and never applies coding automatically.
- `Southern Daily Operations Control Refresh`: active.
- Existing accounting daily controls: active.

Do not enable any external product worker unless:

- the Odoo approval state is approved;
- the worker reports at least `2.0 GB` free disk;
- no duplicate worker is running;
- the cooldown has expired;
- the artifact URI, SHA-256 hash, schema version, and command ID will be written
  back to the Odoo run ledger.

## Post-deployment Smoke Test

Perform read-only checks first:

1. Open **Southern Operations → Daily Control** and refresh one company.
2. Confirm **Product Master Quality** opens without running the full refresh.
3. Confirm **Product Automation Runs** opens and is initially consistent with
   the external worker ledger.
4. Confirm the CRM classification and Contact Import Review menus load.
5. Confirm Equipment Brokerage shows **Discovery Queue**, **Paste Facebook
   Listing**, and **Import Equipment Opportunity CSV**.
6. Confirm Accounting shows Bank Coding Rules, Runs, and Candidates.
7. Confirm existing parts evidence, accounting policies, equipment comp
   analysis, valuation, website, sales, purchase, and service workflows still
   open.
8. Confirm no product was published, repriced, archived, or removed by the
   deployment.

Then execute one controlled candidate-only cycle:

1. Prepare bank coding candidates without approving or applying any candidate.
2. Review the generated counts and rule evidence.
3. Run one supervised product automation batch only after approval and the
   `2.0 GB` safety check.
4. Confirm the artifact hash and run result appear in Odoo.

## Rollback

Stop the rollout immediately on registry failure, unexpected record mutation,
website publication change, access regression, or accounting apply behavior.

1. Stop new scheduled or external worker runs.
2. Capture the failing Odoo.sh build and log evidence.
3. Restore the labeled pre-deployment production backup.
4. Revert the production merge through Git; do not rewrite branch history.
5. Redeploy the restored known-good revision.
6. Verify Accounting, Sales, Inventory, Website, CRM, and Equipment Brokerage
   before reopening automation.

Git rollback alone is not sufficient after module installation because database
schema and data changes may already exist. Restore the database backup as part
of the rollback.
