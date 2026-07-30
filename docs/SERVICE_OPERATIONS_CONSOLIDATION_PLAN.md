# Odoo Service Operations Consolidation Plan

## Purpose

Create one understandable operating flow across:

- Sales
- Client Equipment (`cs_client_equipment`)
- Field Service
- Repairs
- Maintenance (internal equipment)
- Purchase
- Inventory and parts

The goal is not to merge every app into one large custom module. The goal is to
give personnel one connected workflow while retaining the native Odoo modules
that already handle quotations, work execution, inventory moves, purchasing,
and billing.

## Application count versus technical modules

Live inventory on 2026-07-30:

- 404 installed technical Odoo modules.
- 39 installed modules marked as user-facing applications.
- 8 installed Southern/Cyder/DMC custom modules.

For this operating process, personnel currently encounter eight functional
areas:

1. Sales.
2. Client Equipment.
3. Field Service.
4. Repairs.
5. Maintenance.
6. Purchase.
7. Inventory/Parts.
8. Rental and Rental Inspections.

The target navigation reduces this to five primary applications:

1. Sales.
2. Service.
3. Purchase.
4. Inventory.
5. Rental.

The Service integration has 13 direct installed dependencies.
Adding `southern_service_operations` makes 14 direct technical components in
that stack. Those modules remain installed even though personnel experience
one combined application; connector add-ons continue supplying native Sales,
Field Service, Repair, Maintenance, Purchase, and Inventory behavior.

## Current-state evidence

Live read-only audit performed on 2026-07-30:

| Area | Evidence | Operational effect |
|---|---:|---|
| Sales | 65 orders/quotations; 9 stale quotations; 11 confirmed orders to invoice | Follow-up and invoice readiness are not managed as one queue |
| Field Service | 69 tasks; 32 have no sales-order link; 60 lack run hours; 1 is unassigned and overdue | Commercial, equipment, and dispatch context is incomplete |
| Repairs | 1 repair order; no serial/lot and no sales-order link | Repair history cannot reliably follow the unit or billing record |
| Purchase | 188 POs/RFQs; 102 without buyer; 43 confirmed POs without origin; 108 ready to bill | Ownership and demand traceability are weak |
| Sales-to-Purchase | No purchase lines linked to a sales line | Customer-specific demand is not traceable through procurement |
| Maintenance | Installed, but 0 equipment and 0 maintenance requests | Internal preventive/corrective maintenance is not yet operating in Odoo |
| Client Equipment | A dedicated `equipment.details` model exists and links to Contacts through optional field `client` | A customer-equipment master exists, but the customer relationship is not enforced |

Additional structural findings:

- `equipment.details` is supplied by `cs_client_equipment`.
- Client Equipment includes manufacturer, model, serial number, product,
  systems, documents, and job history.
- `equipment.jobs.task_id` is an integer rather than a relational link to
  `project.task`.
- Field Service stores equipment name and serial as duplicate character fields
  (`dmc_equipment`, `dmc_serial_number`) rather than linking to Client Equipment.
- Client Equipment has no direct relational fields to Field Service, Repairs,
  Maintenance, Sales, or Purchase.
- The API audit user cannot read Client Equipment records because it lacks the
  Client Equipment User or Administrator group. Record-level completeness must
  be measured after that access is granted.

## Target operating model

### System-of-record boundaries

| Information or transaction | System of record | Why |
|---|---|---|
| Customer and site contacts | Contacts (`res.partner`) | One customer identity for selling, servicing, and billing |
| Customer-owned equipment | Client Equipment (`equipment.details`) | Already contains customer-equipment attributes and job history |
| Company-owned equipment | Maintenance Equipment (`maintenance.equipment`) | Native basis for preventive and corrective internal maintenance |
| On-site work | Field Service (`project.task` with `is_fsm`) | Scheduling, mobile work, worksheets, time, materials, and customer signoff |
| Shop/bench repair | Repairs (`repair.order`) | Product/serial intake, parts consumption, repair inventory moves, and return |
| Estimate, authorization, and customer billing | Sales | Native quotation, order, pricing, approval, and invoice flow |
| Vendor sourcing and replenishment | Purchase | Native RFQ/PO, receipt, vendor bill, and procurement flow |
| Stocked and consumed parts | Product + Inventory | One product master and one inventory ledger |

### Ownership rule

Customer Equipment and Maintenance Equipment must not be collapsed into the
same model.

- Use Client Equipment when the unit is owned by a customer and Southern
  Equipment is selling or performing work on it.
- Use Maintenance Equipment when the unit is owned or operated internally and
  Southern Equipment is responsible for its preventive/corrective maintenance.
- If ownership changes, use a controlled conversion action that creates the
  appropriate target record and preserves a cross-reference. Do not silently
  change the semantic meaning of an existing record.

### Work-type routing

| Work type | Primary work order | Required asset link |
|---|---|---|
| Customer work performed on site | Field Service task | Client Equipment |
| Customer unit repaired in the shop | Repair Order | Client Equipment and product/serial when applicable |
| Internal preventive/corrective work | Maintenance Request | Maintenance Equipment |
| Inspection-only visit | Field Service task or Rental Inspection, based on ownership/use | Corresponding Client or Rental equipment record |
| Parts-only sale | Sales Order | Equipment optional |
| Customer-specific special-order part | Sales Order + linked procurement | Sales line; Client Equipment when fitment matters |

## Consolidation architecture

Create one unified application add-on: `southern_service_operations`.

For personnel, this is the single **Service** application. Its
menus, intake, equipment lookup, quotation actions, work queues, parts requests,
and reporting cover customer shop work, customer on-site work, and internal
maintenance.

Technically, the application preserves two native execution engines:

- `project.task` for on-site Field Service execution.
- `repair.order` for in-shop Repair execution and repair inventory logistics.
- `maintenance.request` for internal preventive and corrective maintenance.

This is an intentional separation inside one module, not two competing user
workflows. It preserves Odoo's scheduling/mobile Field Service behavior and its
repair-specific reservation, consumption, product movement, and customer-return
behavior.

It should depend on the installed native connectors and the two relevant custom
modules, but it should not copy their business logic:

- `sale_management`
- `industry_fsm`
- `industry_fsm_sale`
- `industry_fsm_stock`
- `repair`
- `purchase`
- `purchase_stock`
- `purchase_repair`
- `sale_purchase`
- `maintenance`
- `cs_client_equipment`
- `dmc_fieldservice` during the transition period

The integration add-on owns:

1. One Service application menu and dashboard.
2. One lightweight Customer Service Case coordination header.
3. One **New Customer Service** intake action.
4. Shared relational fields and service terminology.
5. Relational links between records.
6. Cross-module defaults and consistency checks.
7. Smart buttons and navigation.
8. Role-specific exception queues.
9. Migration helpers for legacy free-text equipment and job references.

It must not own:

- A second customer, equipment, product, stock, repair, task, PO, or invoice model.
- A replacement execution work order. The Service Case coordinates native work;
  it does not own timesheets, inventory moves, repair components, or maintenance
  execution.
- Replacement sales, repair, dispatch, maintenance, or purchasing state machines.
- Duplicated price, tax, inventory, or accounting calculations.

### Unified Service experience

The Service application presents:

- Dashboard
- Customer Service
  - New Customer Service
  - All Service Cases
  - Dispatch / On-site
  - Shop Queue
  - Waiting for Customer
  - Ready to Invoice
- Internal Maintenance
  - New Maintenance Request
  - Maintenance Equipment
  - Preventive Schedule
  - Corrective Queue
  - Due / Overdue
- Shared Operations
  - Waiting for Parts
  - Parts Requests
  - Technician Schedule
  - Purchase Tracking
- Equipment
  - Client Equipment
  - Internal Equipment
- Reporting
- Configuration

**New Customer Service** creates a `southern.service.case` coordination header
and asks for:

- Customer
- Client Equipment
- Complaint/request
- Work location: On-site, Shop, or Hybrid
- Warranty/contract context
- Priority and requested date
- Whether an estimate is required before scheduling

Routing behavior:

- **On-site** routes the Service Case to a Field Service task.
- **Shop** routes the Service Case to a Repair Order.
- **Hybrid** links both Field Service and Repair execution records to the same
  Service Case only when both types of execution are actually required.

The Service Case owns the case number, customer/equipment context, complaint,
coordination status, commercial links, and cross-module navigation. Native
tasks, repairs, and maintenance requests remain the authoritative execution
records.

Both execution records use the same labels and shared fields for customer,
equipment, complaint, diagnosis, technician, promised date, authorization,
quotation, parts demand, and completion summary. A shared high-level service
status maps native states into:

- Intake
- Diagnosing
- Estimating
- Awaiting Customer
- Ready
- Scheduled
- In Progress
- Waiting for Parts
- Work Complete
- Ready to Invoice
- Invoiced
- Closed
- Cancelled

Native Field Service stages and Repair states remain authoritative for their
own execution. The shared status is used for the combined dashboard and
management reporting.

Maintenance Requests participate in the same technician, parts-demand,
purchase-tracking, waiting-for-parts, and management reporting framework. They
do not inherit customer quotation, customer authorization, customer signature,
or ready-to-invoice behavior.

The combined dashboard must always display the work domain:

- Customer — On-site
- Customer — Shop
- Internal — Preventive
- Internal — Corrective

This prevents a combined application from turning into a combined accounting or
ownership model.

## Canonical links

### Client Equipment

Enhance `equipment.details` with:

- Required `client` for active customer equipment.
- Linked `site_contact_id` (`res.partner`), replacing free-text `site_contact`
  for new records while retaining the old value for migration/audit.
- One-to-many links to Field Service tasks and Repair Orders.
- Computed counts and smart buttons for quotations/orders, tasks, repairs, and
  equipment-specific purchases.
- Active/retired status and a required retirement reason.
- Serial normalization and a duplicate-warning constraint scoped by
  manufacturer/product when serial numbers are present.

### Field Service

Enhance `project.task` with:

- `client_equipment_id` (`equipment.details`).
- A domain limiting equipment to the selected customer or its commercial entity.
- Customer, equipment name, model, serial, and service address defaults from
  Client Equipment.
- Run-hours reading retained as a visit measurement, not equipment identity.
- Smart buttons to equipment history, originating sale, related repair, and
  demand-linked purchases.
- A documented exception reason for internal/non-billable tasks without a sale.

Legacy `dmc_equipment` and `dmc_serial_number` fields remain read-only during
migration and are populated from the linked equipment record. They are removed
only after all active tasks have been migrated and reports no longer depend on
them.

### Repairs

Enhance `repair.order` with:

- `client_equipment_id` (`equipment.details`).
- Customer/product/serial defaults from Client Equipment.
- Constraint that the selected equipment belongs to the selected customer.
- Intake checklist: complaint, condition, accessories received, authorization,
  warranty status, assigned technician, and promised date.
- Smart buttons to equipment history, originating/estimate sale, Field Service
  task when a visit preceded the shop repair, and related purchases.
- A controlled action to create a repair estimate/quotation rather than
  entering unrelated sales orders manually.

### Maintenance

Enhance `maintenance.equipment` only for internal assets:

- Ownership classification fixed to internal for this workflow.
- Product, serial/lot, acquisition PO, acquisition date/cost, location,
  responsible employee/team, and meter-reading context.
- Preventive maintenance plan and next-due values.
- Smart buttons for maintenance requests, parts consumption, and acquisition
  documents.

Enhance `maintenance.request` with:

- Required equipment, owner/team, and planned date before entering an active
  stage.
- Parts-demand link to Purchase/Inventory where native replenishment does not
  already provide it.
- Completion readings and failure/cause/remedy classification.

Maintenance records must never be used as a substitute for customer equipment.

### Sales

Enhance `sale.order.line` with optional `client_equipment_id`:

- Required for configured service products representing work on a specific unit.
- Equipment selection limited to the order customer.
- Equipment context passed to the generated Field Service task.
- Repair estimates retain the originating Repair Order and Client Equipment.
- Multiple equipment units can be quoted on one sales order because the link is
  line-level, not only header-level.

Add operational filters:

- Stale quotation: open more than the configured number of days and no next activity.
- Service sold but task not generated.
- Task complete but service not invoice-ready.
- Order ready to invoice.

### Customer quotation policy

Use Sales as the only customer-facing quotation and commercial authorization
engine.

Sales owns:

- Customer, invoice/delivery contacts, and Client Equipment context.
- Products, labor, parts, fees, discounts, taxes, pricelists, and margin controls.
- Quote templates, sections, notes, exclusions, warranty language, and terms.
- Expiration, approval status, customer signature, deposit/payment, confirmation,
  revision history, and conversion to invoice.
- The final amount and scope presented to the customer.

Operational modules supply quotation inputs without creating competing
commercial documents:

- A Repair Order owns diagnosis, estimated parts/labor quantities, warranty
  assessment, actual consumption, and repair state. Its **Create/Update
  Quotation** action creates or synchronizes a linked Sales quotation.
- A Field Service task owns visit scope, technician time, materials, findings,
  and additional-work requests. Approved additional work becomes linked Sales
  order lines.
- Client Equipment supplies the customer unit, serial, model, site, and service
  history context.
- Maintenance supplies internal scope, time, parts, and cost only. It does not
  create customer quotations unless the record is first routed into a customer
  Repair or Field Service workflow.

Documents that must remain outside Sales:

- Vendor RFQs and vendor pricing in Purchase.
- Internal Maintenance budgets, planned labor, and parts estimates.
- Technician working estimates that have not passed service-advisor review.
- Warranty/internal/no-charge approvals, although their reason and authorized
  zero-charge lines should remain traceable to the Sales document when the work
  is customer-facing.

Quotation synchronization must be controlled:

1. One work scope has one active primary Sales quotation.
2. Repair/Field Service lines carry immutable source references.
3. Draft quotation lines may be refreshed from the source work scope.
4. After a quotation is sent, accepted, or confirmed, scope changes create a
   revision/change order instead of silently rewriting customer-approved lines.
5. Actual technician time and consumed parts do not automatically change a
   fixed-price quote; they are compared for margin reporting.
6. Time-and-material work adds actual approved quantities according to the
   configured invoicing policy.
7. Customer approval is recorded on the Sales quotation/change order, while
   technical approval remains on the source Repair or Field Service record.

#### Quotation-type buttons

Add always-visible workflow buttons to the Quotations list control panel,
immediately beside the standard **New** action:

- **Parts**
- **Service**
- **Equipment Sale**
- **Rental**

The standard **New** action remains available for a general quotation. Internal
Maintenance does not receive a quotation button; customer maintenance work uses
the Service quotation and selects where the work is performed.

All buttons create the same native `sale.order` record. They do not create
separate quotation models. Each button passes a quotation type and defaults that
control:

- Quotation template and default validity.
- Sales team and responsible role.
- Warehouse and fulfillment/procurement policy.
- Whether Client Equipment is required.
- Whether confirmation creates/links a Service Case and routes native execution,
  starts rental fulfillment, or creates normal delivery demand.
- Relevant line/product filters and customer-facing terms.

Implement the buttons with Odoo list-view header actions using
`display="always"` and window-action context. Avoid a custom JavaScript control
panel unless later usability testing proves the native header too crowded.

Add `southern_quote_type` to `sale.order` with these values:

- `general`
- `parts`
- `service`
- `equipment_sale`
- `rental`

Service quotations add `southern_service_location`:

- `onsite`
- `shop`
- `hybrid`

The location determines whether the linked Service Case routes to Field
Service, Repair, or both. It does not change the commercial quotation engine.

The value is required, defaults to `general` for legacy and manually created
records, is searchable/groupable, and becomes read-only after confirmation
unless an Operations Manager deliberately resets the workflow. Changing the
type on a populated draft must warn before replacing templates or defaults and
must never silently delete manually entered lines.

Acceptance criteria:

1. Each upper-left button opens a new quotation form with the correct type and
   defaults.
2. Every quotation remains visible in the standard Sales quotation/order
   reporting and invoice flow.
3. Personnel only see buttons allowed by their role.
4. Confirmation triggers only the workflow belonging to the selected type.
5. Existing quotations migrate to `general` without changing their lines,
   totals, taxes, delivery, or invoice behavior.
6. A quotation cannot be confirmed when its type-specific required context is
   missing (for example, Client Equipment and work location on Service).

### Purchase and Inventory

Use native procurement first:

- Reordering rules for normal stocked parts.
- Make-to-order only for genuinely customer-specific or low-frequency,
  high-value demand. Native MTO preserves the Sales-to-PO link.
- Repair and Field Service material consumption through their native stock
  integrations.

Add explicit service demand fields only where native links are absent:

- `repair_order_id`
- `field_service_task_id`
- `client_equipment_id` as a related reporting field
- demand type: stock, sale, repair, field service, maintenance

Require buyer ownership before PO confirmation. Require a source reference or a
documented stock-replenishment reason. Do not require a sales link on ordinary
stock replenishment.

## Personnel workflow

### Service advisor / sales

1. Select the customer.
2. Select existing Client Equipment or create it once.
3. Record complaint and requested work.
4. Route to an on-site Field Service task or a shop Repair Order.
5. Create or link the estimate/quotation.
6. Obtain approval and confirm the sales document.

### Dispatcher

1. Work from the Dispatch Exceptions queue.
2. Resolve missing assignee, equipment, schedule, or customer authorization.
3. Move only ready tasks into Planned/In Progress.
4. Monitor overdue and rescheduled work.

### Technician

1. Open the task/repair and see customer, equipment, serial, history, complaint,
   and approved scope in one place.
2. Record time, parts, meter/run hours, findings, cause, remedy, photos, and
   customer signoff.
3. Request additional parts or quote authorization without creating disconnected
   documents.
4. Complete the work only after required closeout fields are present.

### Purchasing

1. Work from demand-linked RFQs and replenishment suggestions.
2. See whether demand originates from stock, Sales, Field Service, Repair, or
   Maintenance.
3. Assign a buyer and expected receipt date.
4. Notify the originating work order when a late part affects scheduling.

### Billing / management

1. Work from service-complete, ready-to-invoice orders.
2. Resolve exceptions for incomplete delivery, unapproved additional work, or
   missing time/materials.
3. Review aging queues for stale quotes, overdue tasks, open repairs, late POs,
   and vendor bills waiting on receipts.

## Phased implementation

### Phase 0 — Access and baseline

Deliverables:

- Grant the audit/service integration account Client Equipment read access.
- Export counts and completeness for Client Equipment and Equipment Jobs.
- Identify duplicate serials, missing clients, and invalid integer `task_id`
  references.
- Confirm which personnel groups may view costs, margins, customer data, and
  internal equipment.

Exit criteria:

- Every source model has a record count, completeness profile, and owner.
- No migration begins with unknown access or unmeasured data.

### Phase 1 — Equipment identity and navigation

Deliverables:

- Create `southern_service_operations`.
- Add Client Equipment links to Field Service and Repairs.
- Add customer-equipment smart buttons on Contacts, tasks, and repairs.
- Make client/equipment consistency visible and enforce it at operational
  transitions.
- Backfill links from exact customer + normalized serial matches.
- Route ambiguous matches to a review queue; never auto-merge them.

Exit criteria:

- 100% of active customer service tasks and serialized repairs link to one
  Client Equipment record or carry an approved exception.
- No equipment can be selected for the wrong customer.
- Existing historical text values remain auditable.

### Phase 2 — Work-order routing and dispatch

Deliverables:

- Implement the Service Case and intake with explicit routing to Field Service
  or Repairs.
- Link quote-first and intake-first entry paths to one Service Case.
- Define ready-to-plan and ready-to-complete controls.
- Replace `equipment.jobs.task_id` integer usage with a real
  `project_task_id` relation.
- Add dispatch and repair-intake exception views.

Exit criteria:

- Users do not create a separate Equipment Job and Field Service task for the
  same visit.
- Planned work always has customer, equipment, owner, and schedule.
- Completed work contains the required labor/materials/findings/signoff data.

### Phase 3 — Sales, authorization, and invoicing

Deliverables:

- Link service sales lines to Client Equipment and generated tasks.
- Create repair estimates through a controlled quotation action.
- Add additional-work authorization flow.
- Add service-complete/ready-to-invoice queue.

Exit criteria:

- Every billable task/repair traces to a sales order line.
- Every invoice-ready order traces to completed, authorized work.
- Non-billable/warranty work has an explicit reason.

### Phase 4 — Parts and purchasing

Deliverables:

- Add one shared Parts Request linked to a Service Case, Field Service task,
  Repair Order, or Maintenance Request.
- Classify products into stocked, replenished, and customer-demand/MTO policies.
- Preserve native procurement links for MTO demand.
- Add explicit task/repair/maintenance demand references where native Odoo does
  not provide them.
- Require buyer ownership and source classification.
- Add late-parts impact queue.

Exit criteria:

- Every customer-demand PO line traces to its originating work or sales line.
- Ordinary stock replenishment is not falsely classified as customer demand.
- Technicians and dispatchers can see material availability and expected dates.

### Phase 5 — Internal Maintenance

Deliverables:

- Load active internal equipment into Maintenance Equipment.
- Define preventive maintenance templates, meter rules, teams, and schedules.
- Link acquisition and parts documents.
- Add due/overdue internal-maintenance queue.

Exit criteria:

- Active internal assets have owners, locations, and preventive plans.
- Customer equipment does not appear in internal maintenance queues.
- Completed maintenance captures cause, remedy, parts, time, and meter reading.

### Phase 6 — Retire duplication and stabilize

Deliverables:

- Make legacy free-text equipment identity fields read-only.
- Retire Equipment Jobs as a competing work-order workflow after migrating
  history and links.
- Remove obsolete menus/actions only after usage and dependency checks.
- Add regression tests, migration tests, security tests, and operational KPI
  dashboards.

Exit criteria:

- No active workflow writes equipment identity in more than one place.
- No active work order is duplicated across Equipment Jobs, Field Service, and
  Repairs.
- All smart buttons, domains, permissions, and record rules pass role-based
  acceptance testing.

## Roles and access

Define these functional groups:

- Service Advisor
- Dispatcher
- Technician
- Repair Manager
- Maintenance User
- Maintenance Manager
- Buyer
- Billing
- Operations Manager

Minimum principles:

- Technicians may see customer and work context but not purchasing cost or sales margin unless explicitly authorized.
- Buyers may see demand and required dates without receiving unnecessary
  technician HR data.
- Client Equipment users can read the equipment needed for assigned work.
- Only administrators/managers may merge equipment or perform ownership
  conversion.
- Integration/audit accounts receive read access needed for completeness audits
  and narrowly scoped write access only for supervised migrations.

## Migration controls

- Default all migration tools to dry-run.
- Use exact normalized serial + customer matches for automatic links.
- Treat blank, duplicated, placeholder, or conflicting serials as review items.
- Record source model, source record ID, decision, user, timestamp, and before/
  after values for each mutation.
- Process a supervised record limit per run.
- Retain reversible cross-reference fields until the stabilization phase is
  accepted.

## Acceptance-test scenarios

1. A service advisor selects a customer and can only select that customer's
   equipment.
2. A service product on a quotation creates one Field Service task carrying the
   equipment and sales-line links.
3. A shop repair carries customer, equipment, product/serial, estimate, parts,
   and purchase links.
4. A technician cannot move incomplete work to the controlled completion stage.
5. A dispatcher can identify every unassigned, overdue, unscheduled, or
   equipment-less task in one queue.
6. An MTO part retains the native sales-demand link through RFQ, PO, receipt,
   delivery, and invoice.
7. A manually requested repair part carries the repair/task source without
   pretending to be MTO demand.
8. An internal asset creates Maintenance requests but never appears as customer
   equipment.
9. A customer-asset ownership conversion preserves history and does not rewrite
   old work orders.
10. Each role sees the necessary buttons and fields and is denied restricted
    cost, margin, or administrative actions.

## Decisions required before Phase 1 implementation

1. Confirm `equipment.details` remains the permanent customer-equipment master.
2. Confirm Equipment Jobs will be retired as an active work-order system in
   favor of Field Service and Repairs.
3. Confirm the stage at which customer equipment becomes mandatory: intake,
   quotation confirmation, planning, or work start. Recommended: required before
   planning/work start, with intake drafts allowed.
4. Confirm whether un-serialized small equipment may use an approved
   “unserialized” exception.
5. Grant read access to Client Equipment for the audit/integration account so
   migration volume and data quality can be measured.
