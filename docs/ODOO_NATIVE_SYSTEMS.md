# Odoo-Native Operations Systems

## Decision

Odoo is the operational system of record for product automation control,
product quality, accounting approvals, CRM classification, contact import
review, daily operations control, and equipment discovery.

Shop Boss is retired. Its historical Odoo records and source references remain
available for audit, but no new Shop Boss migration, customer import, invoice,
payment, or reconciliation workflow should be started.

## Ownership Boundary

Odoo owns:

- business records, queue status, ownership, priorities, approvals, and notes;
- automation run status, safety gates, cooldowns, evidence metadata, and hashes;
- product-quality issues and their resolution history;
- CRM record classification and contact match decisions;
- bank-coding rules, candidates, approvals, applications, and run history;
- equipment discovery candidates, visible-source evidence, verification
  checklists, conflicts, and conversion to sourced listings;
- the daily cross-functional control record.

External tools retain:

- signed-in browser collection;
- AWS/SSM worker execution;
- S3 artifact storage;
- large file transformations;
- Git, GitHub Actions, tests, and deployment packaging.

Artifacts use a versioned envelope and manifest, SHA-256 integrity metadata, a
2 GB local free-space floor, a default 90-day local retention policy, and a
verified S3 archive URI before an apply run may be completed successfully.

External workers must create or update the corresponding Odoo run/evidence
record. Local CSV, JSON, JSONL, Markdown, Codex memory, and task logs are
supporting artifacts only; they are not authoritative workflow state.

External product workers use
`scripts/odoo_record_product_automation_run.py` to start and finish their Odoo
run ledger record through the shared supervised write gate. New integrations
use Odoo 19 JSON-2 with API keys; legacy XML-RPC is disabled unless an explicit
temporary migration flag is set.

## Implemented Models

### Product automation and quality

- `southern.parts.catalog.sync`
- `southern.parts.automation.run`
- `southern.parts.order.refresh.queue`
- `southern.product.quality.issue` with work lanes `live_fix`,
  `enrich`, and `release`. The bounded refresh re-checks live products and
  stale open rows first, keeps dismissed exceptions until the facts change,
  and blocks Resolve while the finding is still present. Open Daily Control
  product-issue counts exclude `publication_ready` rows; those appear as the
  ready-to-publish count.

The shared disk safety floor is 2 GB. A catalog run can also be blocked by a
cooldown, next-allowed time, pending approval, or another running workflow.

### Daily operations, CRM, and contacts

- `southern.operations.daily.control`
- `crm.lead.southern_record_class`
- `southern.contact.import.batch`
- `southern.contact.import.line`

The standard CRM pipeline is restricted to actual opportunities. Imported
reference data has a separate action and does not count as pipeline.

### Accounting

- `southern.bank.coding.rule`
- `southern.bank.coding.run`
- `southern.bank.coding.candidate`

The daily scheduled action evaluates approved rules and creates candidates.
It does not apply accounting changes. A manager must approve a candidate, then
invoke Apply Coding. Application is rejected unless exactly one Bank Suspense
Account line exists and the target account is valid for the company.

### Equipment discovery

- `southern.equipment.discovery.candidate`
- `southern.equipment.discovery.evidence`

Browser collection remains external. Odoo stores the candidate, exact visible
facts, evidence, conflicts, and verification checklist. Only a verified,
conflict-free candidate can be converted into an unpublished sourced listing.

## Activation Sequence

1. Upgrade `southern_parts_intelligence`.
2. Upgrade `southern_accounting_guardrails`.
3. Upgrade `southern_equipment_brokerage`.
4. Install `southern_operations_control`.
5. Run CRM reference classification and review the separated result.
6. Review the first bounded Product Master Quality batch. Its daily scheduled
   action is active, overlap-protected, and advances a per-company cursor over
   at most 500 products per company.
7. Configure and approve bank-coding rules. Review at least one complete dry
   candidate cycle before applying any candidate.
8. Update external product and equipment workers to write their run/evidence
   metadata to Odoo.
9. After the first successful Odoo bank-candidate cycle, disable the legacy
   `Odoo Daily Auto Reconcile Agent` Windows scheduled task. Do not operate both
   write paths concurrently.

Public partner-pricing enrollment remains disabled unless
`southern_customer_portal.partner_application_enabled` is explicitly enabled
after a commercial and security review.

No module upgrade automatically imports existing local artifact files or
applies bank-coding candidates.
