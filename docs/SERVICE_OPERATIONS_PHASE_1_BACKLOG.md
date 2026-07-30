# Service — Phase 1 Build Backlog

## Phase 1 outcome

Deliver an installable `southern_service_operations` add-on that:

1. Presents one Service application.
2. Adds the Sales quotation-type buttons approved in the architecture.
3. Establishes Client Equipment as the structured customer-asset link on Sales,
   Field Service, and Repairs.
4. Preserves Maintenance Equipment as the internal-asset master.
5. Introduces the lightweight Service Case coordination header without
   duplicating native execution records.
6. Adds navigation and exception reporting without replacing native Odoo state
   machines.
7. Provides a dry-run migration assessment before any live backfill.

Phase 1 is deliberately foundational. It does not automatically create or
rewrite live Sales, Field Service, Repair, Maintenance, Purchase, Inventory, or
Invoice records.

## Locked architecture decisions

- Sales is the only customer-facing quotation and authorization engine.
- The quotation list receives **Parts**, **Service**, **Equipment Sale**, and
  **Rental** buttons; standard **New** remains General.
- Service uses work location **On-site**, **Shop**, or **Hybrid**.
- Personnel use one Service application.
- Field Service, Repairs, and Maintenance retain their native execution models.
- A Service Case coordinates customer work but does not own time, stock moves,
  repair components, purchasing, or invoicing.
- `equipment.details` is the customer-equipment master.
- `maintenance.equipment` is the internal-equipment master.
- Customer and internal equipment are not merged into one ownership model.
- Existing `dmc_equipment` and `dmc_serial_number` data is preserved during
  migration.

## Preconditions

| ID | Requirement | Evidence required |
|---|---|---|
| PRE-01 | Audit/integration account has Client Equipment read access | API can count and read allowed `equipment.details` and `equipment.jobs` fields |
| PRE-02 | `cs_client_equipment` is present in every build/deployment environment | Apps list and module source/dependency check |
| PRE-03 | A current staging/UAT copy of production is available | Database name/date recorded in the release evidence |
| PRE-04 | Role assignments are approved | Named testers for Service Advisor, Dispatcher, Technician, Maintenance, Buyer, Billing, Operations Manager |
| PRE-05 | Database backup and rollback procedure are verified | Restore test or platform backup evidence |

No live data backfill begins until PRE-01 and PRE-03 through PRE-05 are
satisfied.

## Work packages

### SSO-001 — Module foundation

Create `southern_service_operations` with dependencies:

- `sale_management`
- `sale_stock`
- `sale_purchase`
- `industry_fsm`
- `industry_fsm_sale`
- `industry_fsm_stock`
- `repair`
- `purchase`
- `purchase_stock`
- `purchase_repair`
- `maintenance`
- `cs_client_equipment`
- `dmc_fieldservice`

Deliverables:

- Manifest and initialization.
- Security groups and access seed.
- Configuration menu.
- Feature switches for quotation shortcuts, equipment enforcement, and legacy
  migration visibility.
- Upgrade-safe XML IDs and versioning.

Acceptance:

- Fresh install and module upgrade complete without warnings that affect use.
- Uninstall is not used as a rollback mechanism after data fields are deployed.
- Disabling feature switches removes new enforcement while retaining data.

### SSO-002 — Roles and security

Groups:

- Service Advisor
- Dispatcher
- Technician
- Service Manager
- Maintenance User
- Maintenance Manager
- Operations Manager

Rules:

- Service personnel can read Client Equipment necessary for assigned/customer
  work.
- Technicians cannot see Sales margin or Purchase cost unless another existing
  group grants it.
- Maintenance users do not gain access to unrelated customer commercial data.
- Only Service/Operations Managers may approve equipment mismatch exceptions,
  merge duplicates, or reset a confirmed workflow type.

Acceptance:

- Each role passes positive and negative access tests.
- No group grants broader Accounting, HR, Sales margin, or Purchase cost access
  indirectly.

### SSO-003 — Quotation type and upper-left actions

Add to `sale.order`:

| Field | Type | Required | Rule |
|---|---|---:|---|
| `southern_quote_type` | Selection | Yes | General, Parts, Service, Equipment Sale, Rental |
| `southern_service_location` | Selection | Conditional | On-site, Shop, Hybrid; visible/required only for Service |
| `southern_client_equipment_id` | Many2one `equipment.details` | Conditional | Customer equipment; required for Service before controlled confirmation unless exception is approved |
| `southern_equipment_exception_reason` | Text | Conditional | Manager-controlled exception for legitimate unserialized/unregistered work |

Add always-visible Sales quotation list header actions:

- Parts
- Service
- Equipment Sale
- Rental

Each action opens a new native `sale.order` form with the correct context
defaults. Existing records receive `general` without recomputing lines or totals.

Draft behavior:

- Type can change while the draft has no commercial lines.
- Changing a populated draft requires explicit confirmation.
- Type changes never delete lines.
- Type becomes read-only after confirmation except for Operations Manager reset.

Acceptance:

- Buttons render beside the standard control-panel actions in list and kanban
  quotation views where supported.
- Each button creates a new unsaved form with the correct type.
- Service additionally requires a work location.
- Phase 1 does not silently create Service Cases, tasks, or repairs on
  confirmation while custom routing is disabled.
- Existing quotation totals, taxes, deliveries, invoices, and states are
  unchanged after upgrade.

### SSO-004 — Client Equipment canonical link

Extend `equipment.details`:

| Field | Type | Purpose |
|---|---|---|
| `southern_active` | Boolean | Active/retired lifecycle without deleting history |
| `southern_retirement_reason` | Text | Required when retiring |
| `southern_site_contact_id` | Many2one `res.partner` | Relational replacement for free-text site contact |
| `southern_service_task_ids` | One2many `project.task` | Field Service history |
| `southern_repair_order_ids` | One2many `repair.order` | Shop history |
| computed counts | Integer | Smart-button counts |

Controls:

- `client` is required before equipment is active or used on planned customer
  work.
- Client and site contact use the same commercial entity unless a manager
  approves an exception.
- Normalized serial duplicate detection warns on likely duplicates; it does not
  auto-merge.
- Existing free-text `site_contact` remains visible in migration/audit context.

Acceptance:

- A Contact opens only its Client Equipment records.
- Equipment opens its Field Service and Repair history.
- Retiring equipment never deletes or unlinks historical work.

### SSO-005 — Field Service integration

Add to `project.task`:

| Field | Type | Purpose |
|---|---|---|
| `southern_client_equipment_id` | Many2one `equipment.details` | Canonical customer asset |
| `southern_equipment_exception_reason` | Text | Controlled exception |
| `southern_service_domain` | Selection | Customer on-site; supports combined reporting |
| `southern_high_level_status` | Selection/computed | Shared dashboard state |

Behavior:

- Equipment domain follows task customer/commercial entity.
- Selecting equipment defaults the customer and legacy display fields only when
  safe and unambiguous.
- `dmc_equipment` and `dmc_serial_number` become related/read-only display
  values for migrated active work after rollout validation.
- `dmc_equipment_run_hours` remains an editable visit meter reading.
- Planning/In Progress is blocked when customer, equipment/exception, assignee,
  or schedule is missing.

Acceptance:

- Wrong-customer equipment cannot be selected.
- Existing 69 Field Service tasks remain readable and retain their stages.
- The current unassigned/overdue exception is visible in a Dispatcher queue.
- A migrated task opens the linked Client Equipment history.

### SSO-006 — Repair integration

Add to `repair.order`:

| Field | Type | Purpose |
|---|---|---|
| `southern_client_equipment_id` | Many2one `equipment.details` | Canonical customer asset |
| `southern_equipment_exception_reason` | Text | Bench/unserialized exception |
| `southern_service_domain` | Selection | Customer shop |
| `southern_high_level_status` | Selection/computed | Shared dashboard state |
| `southern_field_service_task_id` | Many2one `project.task` | Preceding/related site visit |

Behavior:

- Equipment domain follows repair customer.
- Selecting equipment defaults product and serial/lot only when the relationship
  is exact and valid.
- Repair confirmation requires customer, product, equipment/exception, and
  responsible person.
- Existing native sale and purchase links remain authoritative.

Acceptance:

- The existing repair remains intact and appears in the Shop Queue.
- Its missing serial/equipment condition is reported, not guessed.
- A repair can navigate to equipment history and a related site visit.

### SSO-007 — Internal Maintenance workspace

Expose native:

- `maintenance.equipment`
- `maintenance.request`
- preventive schedule and due/overdue filters

Add shared reporting fields:

| Field | Model | Purpose |
|---|---|---|
| `southern_service_domain` | `maintenance.request` | Internal preventive or internal corrective |
| `southern_high_level_status` | `maintenance.request` | Shared dashboard state |
| `southern_parts_blocked` | `maintenance.request` | Waiting-for-parts queue |

Phase 1 does not load internal equipment. It prepares the controlled workspace
and import template for Phase 5 of the consolidation plan.

Acceptance:

- Customer Equipment cannot be selected as Maintenance Equipment.
- Maintenance users see internal menus without customer Ready-to-Invoice actions.
- Empty-state guidance explains how internal assets will be loaded.

### SSO-008 — Service Case foundation

Create `southern.service.case` as a coordination header, not an execution work
order.

Core fields:

| Field | Type | Purpose |
|---|---|---|
| `name` | Char/sequence | Stable customer-service case number |
| `partner_id` | Many2one `res.partner` | Customer |
| `client_equipment_id` | Many2one `equipment.details` | Customer asset |
| `service_location` | Selection | On-site, Shop, Hybrid |
| `complaint` | Text | Customer request/problem |
| `advisor_id` | Many2one `res.users` | Service coordinator |
| `priority` | Selection | Operational priority |
| `requested_date` | Datetime | Customer/requested timing |
| `commercial_basis` | Selection | Estimate, pre-authorized, warranty, contract, no-charge |
| `state` | Selection | Shared coordination state |
| `sale_order_id` | Many2one `sale.order` | Primary quotation/order |
| `task_ids` | One2many `project.task` | On-site execution |
| `repair_order_ids` | One2many `repair.order` | Shop execution |

Phase 1 behavior:

- New Customer Service creates a draft Service Case.
- Users may link existing Sales, Field Service, and Repair records.
- Customer/equipment consistency is enforced.
- Smart buttons open native source records.
- Automatic quote-confirmation routing and automatic task/repair creation remain
  disabled until Phase 2 UAT.

The Service Case must not contain:

- Timesheet lines.
- Repair component lines.
- Inventory moves.
- Purchase Order lines.
- Invoice lines.
- A competing execution state machine.

Acceptance:

- One case can coordinate On-site, Shop, or legitimate Hybrid execution.
- Native task/repair state remains authoritative.
- Linking a record does not copy or recompute its quantities, prices, stock, or
  accounting.
- Duplicate active cases for the same primary source are detected.

### SSO-009 — Unified menus and queues

Application menus:

- Dashboard
- Customer Service
- Internal Maintenance
- Shared Operations
- Equipment
- Reporting
- Configuration

Required queues:

- Customer On-site
- Customer Shop
- Internal Preventive
- Internal Corrective
- Waiting for Customer
- Waiting for Parts
- Dispatch Exceptions
- Ready to Invoice

Every combined card/list row displays its work domain. Source-specific actions
open the native Field Service task, Repair Order, or Maintenance Request.

Acceptance:

- Users reach the most common queue within two clicks of the application.
- Combined reporting never hides whether a record is customer or internal work.
- No duplicate work record is created merely to display a combined queue.

### SSO-010 — Migration assessment and review queue

Create a read-only/dry-run assessment that:

1. Reads Client Equipment, Equipment Jobs, active Field Service tasks, and
   Repair Orders.
2. Normalizes customer and serial values without modifying source data.
3. Proposes links using exact commercial entity + exact normalized serial.
4. Classifies each proposal:
   - exact
   - ambiguous
   - missing equipment
   - missing customer
   - conflicting customer
   - placeholder/invalid serial
5. Validates legacy `equipment.jobs.task_id` values against `project.task`.
6. Writes a versioned JSON/CSV report with hashes and record counts.

No fuzzy match is auto-applied.

Acceptance:

- Repeated dry runs produce the same decisions against unchanged data.
- Every proposed link contains source IDs and evidence.
- Ambiguous/conflicting rows are never included in an apply set.
- The apply workflow, when later approved, uses the repository ApplyGate and
  supervised record limits.

### SSO-011 — Automated and role-based verification

Required automated coverage:

- Python compilation.
- XML parsing.
- Manifest dependency validation.
- Model constraint tests.
- Quotation default/action tests.
- Customer-equipment domain tests.
- Legacy record upgrade tests.
- Migration matching/idempotency tests.
- Access-right and record-rule tests.
- Native workflow smoke tests for Sales confirmation, Field Service creation,
  Repair confirmation, and Maintenance request creation.

Required UAT scenarios:

1. Parts quote.
2. On-site Service quote.
3. Shop Service quote.
4. Hybrid Service quote.
5. Equipment Sale quote.
6. Rental quote.
7. Existing legacy quotation.
8. Existing Field Service task with exact equipment match.
9. Ambiguous equipment match.
10. Existing Repair with missing serial.
11. Internal Maintenance request.
12. Technician cost/margin denial.

## Phase 1 rollout

1. Install/upgrade in staging with enforcement disabled.
2. Run automated tests and role-based UAT.
3. Run the full dry-run equipment migration assessment.
4. Resolve access and data-quality blockers.
5. Enable quotation shortcuts and unified menus in staging.
6. Enable equipment links without transition blockers.
7. Backfill only exact approved links under supervised controls.
8. Enable planning/confirmation controls after exception queues are cleared.
9. Deploy the identical tested version to production.
10. Monitor failures, queue counts, and user feedback daily for the first week.

## Explicitly deferred beyond Phase 1

- Automatic quotation-line synchronization from Repairs/Field Service.
- Automatic quotation confirmation → Service Case → execution routing.
- Customer change-order workflow.
- Shared Parts Request and service-demand Purchase workflow.
- Automatic demand-linked PO creation beyond native procurement.
- Buyer/source enforcement on existing Purchase Orders.
- Internal Maintenance asset import and preventive plan activation.
- Retirement of Equipment Jobs.
- Removal of legacy DMC equipment fields.
- Consolidated financial KPI dashboard.

These items depend on the structured links and clean migration evidence produced
by Phase 1.

## Approval gate

Phase 1 implementation should begin only after confirming:

1. `equipment.details` remains the customer-equipment master.
2. Existing Equipment Jobs will eventually be retired as a competing work-order
   workflow.
3. Equipment becomes mandatory before work enters Planned/In Progress, while
   draft intake may use a manager-reviewed exception.
4. Legitimately unserialized equipment may use an explicit exception instead
   of a fabricated serial number.
5. The audit/integration account receives Client Equipment read access.
