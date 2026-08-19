# Learning process overview

This is the first-stop map of Titan ERP: what Odoo owns, how Southern
Equipment actually operates, and the gates every change must pass. Read this
before changing modules, workers, or production data.

Odoo 19 is the system of record. Shop Boss is retired. Local CSV, JSON, JSONL,
Markdown, Codex memory, and task logs are supporting artifacts only.

## How to learn this repository

Study in this order. Do not skip the safety contract.

1. This document, then [README.md](../README.md).
2. [Odoo-native ownership](ODOO_NATIVE_SYSTEMS.md) — what Odoo owns vs what
   workers may do.
3. [Production runbook](ODOO_PRODUCTION_RUNBOOK.md) — deploy, smoke-test, and
   rollback rules.
4. The add-on README closest to the process you are changing.
5. The add-on `__manifest__.py`, models, security, and scheduled actions.
6. The matching `tests/test_*.py` file and any module tests under the add-on.
7. Only then the worker script or AWS unit that executes the process.

If two sources disagree, prefer the Odoo model and its tests over a worker
comment, and prefer a dated runbook over an older add-on README.

## Operating rules

These rules are invariant. A change that violates them is incomplete.

- Odoo owns business records, queue status, approvals, notes, run ledgers,
  evidence metadata, and daily control counts.
- External tools own signed-in browser collection, AWS/SSM execution, S3
  artifacts, large file transforms, Git, and packaging.
- New external integrations use Odoo 19 JSON-2 with API keys. XML-RPC is off
  unless `ODOO_ALLOW_LEGACY_XMLRPC=true` for an approved migration window.
- Workers start and finish an Odoo run through
  `scripts/odoo_record_product_automation_run.py` and the shared write gate.
- Production writes require all of: `ODOO_WRITE_ENABLED=true`, `--apply`,
  `--confirm <exact-workflow-name>`, a business `--reason`, and a record count
  inside `--max-records`.
- Artifacts use a versioned envelope, SHA-256, a 2 GB free-space floor, default
  90-day local retention, and a verified S3 archive URI before an apply run may
  finish successfully.
- Downstream stages take an explicit input path plus its SHA-256. Never select
  “latest” files.
- Scheduled actions do not publish products, change live prices, approve
  accounting, or apply contact imports.
- No module upgrade imports local artifacts or applies bank-coding candidates.
- Never commit API keys, AWS credentials, OpenAI keys, or dumps. Copy
  `odoo_connection.env.example` only to an ignored local file.
- Do not run the AWS product worker as account root. Confirm
  `aws sts get-caller-identity` shows a named least-privilege role.
- Production merges target `main-production` after a reviewed PR, GitHub
  validation, and a successful Odoo.sh test build.

## Company process map

Southern Equipment Company runs one Odoo 19 Enterprise database. Custom add-ons
coordinate native Odoo apps; they do not replace Sales, Accounting, Inventory,
Field Service, Repairs, Maintenance, or Purchase.

```text
Daily operations
  southern.operations.daily.control
    -> bank, invoices, CRM, service, product, equipment, contact exceptions

Sales workspace
  Parts quote | Service job | Equipment sale | Rental
    Service -> southern.service.case
      on-site  -> Field Service project.task
      shop     -> repair.order
      internal -> maintenance.request
      parts    -> purchase.order lines linked to the case

Parts catalog
  Sparex discovery -> match/classify -> evidence -> supplier cost
    -> retail/margin -> quality -> website publication

Equipment brokerage
  browser collect -> discovery candidate -> verify
    -> unpublished sourced listing -> publish -> inquiry -> deal

Accounting
  policies + revenue rules + bank-coding candidates
    -> manager approve -> apply one Bank Suspense line
    Stripe Terminal / cash / ACH register native payments

Customer portal
  membership, repair orders, open invoices
  public partner-pricing enrollment stays off unless explicitly enabled
```

## Add-on map

Install or upgrade in the order the runbook names. Versions below are the
manifest versions in this checkout; bump the changed add-on when you ship.

| Add-on | Role |
| --- | --- |
| `southern_parts_intelligence` | Parts catalog, Sparex discovery/sourcing, evidence, quality, automation ledger |
| `southern_service_operations` | Sales-hosted Service. Coordination only; native execution records stay authoritative |
| `southern_accounting_guardrails` | Revenue buckets, bank-coding approvals, Shop Boss audit, accounting daily control |
| `southern_equipment_brokerage` | Sourced equipment listings, discovery, inquiries, deals, comps |
| `southern_operations_control` | Cross-functional daily control, CRM class, contact-import review |
| `southern_customer_portal` | Portal membership, repairs, invoices; partner application disabled by default |
| `southern_stripe_terminal` | Card-present invoice balance to Stripe Terminal; native payment register |
| `cs_client_equipment` | Customer-owned equipment master (`equipment.details`) |
| `cs_rental_inspection` | Rental pickup/return inspections |
| `dmc_fieldservice` | Equipment / serial / hours on Field Service tasks |
| `dmc_company_setup_wizard` | New-company accounting setup wizard |
| `l10n_us_hr_payroll_ms_status` | Mississippi payroll withholding statuses |

### Service

Sales is the personnel workspace. `southern.service.case` is the header.

- Customer equipment is `equipment.details` and must match the commercial
  partner on the case, quotation, Field Service task, and repair.
- Company equipment is `maintenance.equipment`.
- Routing is idempotent: on-site, shop, hybrid, or internal.
- Shop routing requires a real product mapping on the equipment.
- AI estimates are review-only. They cannot send, confirm, purchase, deliver,
  invoice, or silently edit a quotation. Production deployment of Service is
  still a no-go until the operational gates in
  [SERVICE_PRODUCTION_READINESS.md](SERVICE_PRODUCTION_READINESS.md) are signed.

### Parts and Sparex

Two complementary pipelines share the same publication gates.

**Discovery** inventories authenticated Sparex listing pages and classifies
exact `S.%` SKUs. It does not invent cost, price, image bytes, taxonomy, or
publication state. Missing drafts, when separately approved, are unpublished
and capped at five per checkpoint. See
[SPAREX_CATALOG_DISCOVERY.md](SPAREX_CATALOG_DISCOVERY.md).

**Sourcing / release** requires exact SKU match, current positive Sparex
supplier cost in company currency, exact HTTPS evidence URL and SHA-256 not
older than 30 days, approved retail that meets margin, website category, image,
and customer-facing description. Cost writes `product.supplierinfo` only — never
`product.template.standard_price`. See
[SPAREX_SOURCING_CONTROL.md](SPAREX_SOURCING_CONTROL.md).

The durable catalog pipeline (SQS FIFO, media worker, promotion worker) keeps
vendor access, immutable evidence, Odoo readiness, and website publication as
separate states. Nothing in that stack auto-enables. See
[sparex-durable-catalog-pipeline.md](sparex-durable-catalog-pipeline.md).

Five catalog-agent profiles (Coordinator, Discovery, Match, Verification,
Release) are deterministic in production. Optional OpenAI review is only for
marked ambiguous exceptions and is disabled on the scheduled launcher.

### Equipment brokerage

Browser collection stays external. Odoo stores the candidate, visible facts,
evidence, conflicts, and checklist. Only a verified, conflict-free candidate
becomes an unpublished sourced listing. Website publication needs public-safe
status, region, verification note, reviewed photo, and recorded publication
rights. Source, seller, serial, and margin stay internal.

### Accounting

Bank-coding cron creates candidates only. A manager approves, then Apply
Coding. Application fails unless exactly one Bank Suspense Account line exists
and the target account is valid for the company. Guardrails do not post,
reconcile, or delete entries. After the first successful Odoo candidate cycle,
disable the legacy Windows `Odoo Daily Auto Reconcile Agent`. Do not run both
write paths.

Stripe Terminal sends the exact posted residual, then registers payment only
after a succeeded PaymentIntent. It never rewrites invoice totals or journal
lines.

### Daily operations

`southern.operations.daily.control` is one record per company per day. It
refreshes exception counts for unreconciled bank lines, pending coding
candidates, draft invoices, orders to invoice, open quotations, open service
tasks, actual vs imported CRM, stale CRM, overdue activities, product issues,
automation failures, equipment review, and contact-match review.

CRM: the standard pipeline is actual opportunities only. Imported reference
data is classified separately and does not count as pipeline.

## Shared automation runtime

`scripts/odoo_runtime` is the only supported worker library.

| Module | Job |
| --- | --- |
| `safety.ApplyGate` | Dry-run vs apply, env write window, confirm string, reason, batch bound, idempotency key |
| `client` | JSON-2 first; XML-RPC only behind the legacy flag |
| `artifacts.ArtifactStore` | 2 GB floor, versioned envelope, SHA-256, retention |
| `crm` / `matching` | CRM class and contact-match decisions used by operations import |

Product workers record runs with `scripts/odoo_record_product_automation_run.py`.
AWS credential cutover is manual; this repo does not create IAM users. See
[AWS_AUTOMATION_CREDENTIAL_MIGRATION.md](AWS_AUTOMATION_CREDENTIAL_MIGRATION.md).

## How to change the system

1. Scope one Odoo business process. Do not mix Service, catalog, and
   accounting writes in one change.
2. Prefer additive models, nullable links, and native records over a second
   engine for sales, procurement, or accounting.
3. Bump every upgraded add-on version in `__manifest__.py`.
4. Keep scheduled actions bounded, overlap-protected, and company-scoped.
5. Put write-capable workers behind `ApplyGate`. Default is dry-run.
6. Add or extend standalone tests under `tests/` and any add-on tests.
7. Run local validation:

   ```powershell
   py -3.12 -m venv .venv
   .\.venv\Scripts\python.exe -m pip install -e ".[dev,aws]"
   .\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
   .\.venv\Scripts\ruff.exe check scripts tests --select E4,E7,E9,F,I
   ```

8. Confirm GitHub workflow `.github/workflows/validate.yml` (compile, unittest,
   ruff, XML parse, secret scan) and an Odoo.sh test build.
9. Pause product/catalog/AWS workers before production module deploy. Keep
   `ODOO_WRITE_ENABLED` unset during the upgrade.
10. Take a labeled Odoo.sh backup. Git revert alone is not enough after a
    schema-changing upgrade.

Use [the pull request template](../.github/pull_request_template.md) as the
change checklist.

## Where the detailed contracts live

| Process | Document |
| --- | --- |
| Ownership and models | [ODOO_NATIVE_SYSTEMS.md](ODOO_NATIVE_SYSTEMS.md) |
| Production deploy / rollback | [ODOO_PRODUCTION_RUNBOOK.md](ODOO_PRODUCTION_RUNBOOK.md) |
| Service UAT and go/no-go | [SERVICE_PRODUCTION_READINESS.md](SERVICE_PRODUCTION_READINESS.md) |
| Sparex ten-product sourcing | [SPAREX_SOURCING_CONTROL.md](SPAREX_SOURCING_CONTROL.md) |
| Sparex listing inventory | [SPAREX_CATALOG_DISCOVERY.md](SPAREX_CATALOG_DISCOVERY.md) |
| SQS durable catalog | [sparex-durable-catalog-pipeline.md](sparex-durable-catalog-pipeline.md) |
| Worker IAM | [AWS_AUTOMATION_CREDENTIAL_MIGRATION.md](AWS_AUTOMATION_CREDENTIAL_MIGRATION.md) |
| Catalog agent profiles | [scripts/sparex_catalog_agents/README.md](../scripts/sparex_catalog_agents/README.md) |
