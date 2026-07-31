# Service — Detailed Future-State Process

## 1. Operating concept

Southern Equipment personnel work from two connected Odoo applications:

1. **Sales** owns every customer-facing quotation, commercial authorization,
   sales order, and invoice relationship.
2. **Service** owns customer service intake, work coordination,
   execution visibility, internal maintenance, parts demand, and operational
   queues.

The Service application combines the user experience for:

- Customer field service.
- Customer shop repair.
- Internal preventive maintenance.
- Internal corrective maintenance.

It does not force those processes into one technical transaction. Odoo retains:

- Field Service tasks for on-site execution.
- Repair Orders for shop execution and repair inventory movements.
- Maintenance Requests for company-owned equipment.
- Purchase Orders for vendor commitments.
- Sales Orders for customer commitments and billing.

## 2. Record architecture

### 2.1 Customer Service Case

Create a lightweight `southern.service.case` record as the coordination header
for customer service.

The Service Case is not a replacement work order. It does not own timesheets,
inventory movements, repair components, quotations, invoices, or purchase
orders. It connects those native records and gives personnel one case number
and one place to understand the complete job.

Required fields:

- Case number.
- Customer.
- Client Equipment or approved exception.
- Work location: On-site, Shop, or Hybrid.
- Complaint/request.
- Service advisor/coordinator.
- Priority.
- Requested date.
- Commercial requirement: Estimate required, pre-authorized, warranty,
  contract, or no-charge.

Linked records:

- Primary Sales quotation/order.
- Change-order quotations.
- Field Service task(s).
- Repair Order(s).
- Parts requests.
- Purchase Order lines.
- Deliveries/receipts relevant to the work.
- Customer invoice(s).
- Attachments, photos, messages, and activities.

Computed information:

- High-level case status.
- Parts availability/blocking status.
- Customer authorization status.
- Work completion status.
- Invoice readiness/status.
- Total quoted, invoiced, labor time, material cost, and margin, subject to
  access rights.

### 2.2 Customer Equipment

`equipment.details` is the master record for customer-owned equipment.

It owns:

- Customer/commercial entity.
- Site contact and service address.
- Equipment category.
- Manufacturer.
- Model.
- Serial number.
- Product relationship.
- Systems/components.
- Documents.
- Active/retired status.
- Customer service history through linked cases, tasks, and repairs.

It does not own:

- Technician time.
- Parts consumption.
- Customer pricing.
- Vendor purchasing.
- Internal preventive maintenance.

### 2.3 Internal Equipment

`maintenance.equipment` is the master for company-owned or internally operated
equipment.

It owns:

- Internal asset identity.
- Responsible employee/team.
- Location.
- Serial/meter context.
- Preventive maintenance plan.
- Maintenance history.
- Acquisition context.

Customer Equipment never appears in internal Maintenance queues, and internal
equipment never appears on customer quotations.

## 3. Application navigation

### 3.1 Sales

The Quotations screen retains the standard **New** action and adds:

- **Parts**
- **Service**
- **Equipment Sale**
- **Rental**

All actions create the native `sale.order` model.

### 3.2 Service

Top-level menus:

- Dashboard
- Customer Service
- Internal Maintenance
- Shared Operations
- Equipment
- Reporting
- Configuration

Customer Service:

- New Customer Service
- All Service Cases
- On-site / Dispatch
- Shop Queue
- Awaiting Estimate
- Waiting for Customer
- Waiting for Parts
- Ready to Invoice
- Closed Cases

Internal Maintenance:

- New Maintenance Request
- Internal Equipment
- Preventive Schedule
- Corrective Queue
- Due / Overdue
- Waiting for Parts
- Completed

Shared Operations:

- Technician Schedule
- Parts Requests
- Purchase Tracking
- Parts Received / Work Ready
- Operational Exceptions

Equipment:

- Client Equipment
- Internal Equipment
- Duplicate/Incomplete Equipment Review

### 3.3 Cross-application access

Sales and Service are bidirectionally connected, but they remain
separate Odoo applications.

From a Sales quotation/order, authorized users can open:

- The linked Service Case.
- Client Equipment.
- Field Service task(s).
- Repair Order(s).
- Change orders.
- Parts-demand/Purchase tracking relevant to the customer work.

From a Service Case, Field Service task, or Repair Order, authorized users can
open:

- The primary quotation/Sales Order.
- Change-order quotations.
- Customer invoice(s).
- Client Equipment and complete service history.

Internal Maintenance is not placed inside the Sales workflow:

- Maintenance Equipment and Maintenance Requests remain in Service &
  Maintenance → Internal Maintenance.
- They do not show quotation, customer approval, margin, or Ready-to-Invoice
  actions.
- Authorized managers may navigate from an internal asset to its acquisition
  Purchase Order or product, but not to an artificial customer Sales Order.

The Sales application may include an authorized **Service Cases** shortcut for
service advisors and sales managers. This is a filtered navigation action into
the Service application, not a duplicate maintenance menu or
record set.

## 4. Quote-type behavior

| Quote action | Equipment requirement | Confirmation result | Primary fulfillment |
|---|---|---|---|
| New / General | Optional | Normal Sales behavior | Configured products/routes |
| Parts | Optional; recommended when fitment matters | Delivery and/or demand procurement | Inventory/Purchase |
| Service | Client Equipment required before controlled confirmation unless exception approved | Creates/links Service Case, then routes work by location | Field Service and/or Repairs |
| Equipment Sale | Optional sold-unit context; serial assigned through delivery | Normal equipment delivery and customer ownership handoff | Inventory/Sales |
| Rental | Rental equipment and dates required | Native rental reservation/fulfillment and inspections | Rental/Inventory |

## 5. Customer service entry paths

Customer service may begin from Sales or Service. Both paths must
converge on one Service Case.

### 5.1 Quote-first entry

Use when the customer already knows the requested scope or Southern can quote it
without diagnosis.

1. Service Advisor opens Sales → Quotations.
2. Selects **Service**.
3. Selects customer.
4. Selects Client Equipment.
5. Selects work location:
   - On-site.
   - Shop.
   - Hybrid.
6. Enters complaint/request and service lines.
7. Odoo applies the Service quotation template, validity, terms, and relevant
   product filters.
8. Advisor reviews pricing, scope, exclusions, taxes, and promised date.
9. Sends quotation for customer approval/signature/payment as configured.
10. Customer approval converts it to a Sales Order.
11. Confirmation creates one Service Case if none exists.
12. The Service Case creates or links the correct execution record:
    - On-site → Field Service task.
    - Shop → Repair Order.
    - Hybrid → linked Field Service and Repair records only as required.
13. The case enters **Ready** or **Waiting for Parts** based on material
    availability.

### 5.2 Intake-first entry

Use when equipment arrives at the shop, the customer calls with an unknown
problem, or diagnosis is required before quoting.

1. Service Advisor opens Service → New Customer Service.
2. Selects customer.
3. Searches Client Equipment by serial, equipment name, model, customer, or
   site.
4. If found, selects it.
5. If not found:
   - Creates a properly linked Client Equipment record; or
   - Uses a manager-reviewed temporary exception for legitimate emergency or
     unserialized work.
6. Records complaint, work location, priority, requested date, warranty/contract
   claim, and estimate requirement.
7. Odoo creates the Service Case in **Intake**.
8. Based on work location, Odoo creates:
   - A diagnostic Field Service task; or
   - A diagnostic Repair Order.
9. Technician records diagnosis and estimated labor/parts.
10. Service Advisor selects **Create Quotation** on the Service Case.
11. Odoo creates one linked draft Service quotation using approved estimate
    inputs.
12. Advisor reviews the commercial document and sends it to the customer.
13. The Service Case enters **Waiting for Customer**.
14. Approval changes the case to **Ready** or **Waiting for Parts**.

### 5.3 Existing Sales Order or work record

When personnel begin from an existing Sales Order, Field Service task, or Repair
Order:

1. Odoo searches for an already-linked Service Case.
2. If one exists, it opens/links that case.
3. If none exists, the user may create a case using the existing customer,
   equipment, source document, and complaint.
4. Duplicate detection prevents two active cases for the same source work.

## 6. Service Case status model

| Status | Meaning | Entry control | Exit event |
|---|---|---|---|
| Intake | Request captured but not commercially/operationally ready | Customer, complaint, work location | Triage completed |
| Diagnosing | Technical inspection/diagnosis authorized | Equipment/exception and assigned diagnostic owner | Estimate/scope ready |
| Estimating | Scope is being converted to Sales quotation | Diagnosis or defined scope | Quote sent or no-quote authorization |
| Waiting for Customer | Customer approval, deposit, or decision outstanding | Linked sent quotation/change order | Approved, declined, expired |
| Ready | Authorized and materials available | Customer/equipment, owner, authorization, required parts | Scheduled/start |
| Scheduled | Date/technician committed | Assignee and schedule | Work starts |
| In Progress | Technician actively performing work | Start controls satisfied | Complete or blocked |
| Waiting for Parts | Required material unavailable | Open parts demand | All blocking parts available/issued |
| Waiting for Customer | Additional authorization or customer action needed | Documented requested action | Customer response |
| Work Complete | Technical work and closeout complete | Time, materials, findings, readings, signoff/exception | Commercial review |
| Ready to Invoice | Delivered/completed quantities and authorization support billing | Sales link and billing controls | Invoice created |
| Invoiced | Customer invoice exists | Linked invoice | Paid/administrative closure |
| Closed | Operational and commercial work complete | Closure checklist | Reopen by manager only |
| Cancelled | Work will not proceed | Cancellation reason | Reopen by manager only |

Because **Waiting for Customer** can occur before or during execution, the system
also records a waiting reason:

- Initial estimate approval.
- Additional work/change order.
- Customer-supplied information.
- Customer pickup/delivery decision.
- Payment/deposit.

## 7. Work-location routing

### 7.1 On-site

System action:

- Creates/links an FSM `project.task`.
- Copies customer, equipment, site, complaint, approved scope, and case number.
- Links the Sales Order and Sales Order line.
- Assigns the Field Service project/worksheet.

Required before **Scheduled/In Progress**:

- Customer.
- Client Equipment or approved exception.
- Service address.
- Assignee.
- Planned start.
- Approved/pre-authorized scope.
- Required parts availability or documented proceed-without-parts decision.

Technician captures:

- Arrival/departure.
- Timesheet.
- Run hours/meter reading.
- Materials used.
- Diagnosis/cause/remedy.
- Photos and worksheet.
- Additional work required.
- Customer signature or approved no-signature reason.

### 7.2 Shop

System action:

- Creates/links a `repair.order`.
- Copies customer, equipment, complaint, case number, and commercial context.
- Uses product/lot/serial when the Client Equipment relationship supports an
  exact valid mapping.
- Preserves native repair locations and component reservation.

Required before repair confirmation/start:

- Customer.
- Client Equipment or approved bench/unserialized exception.
- Product to repair.
- Serial/lot when applicable.
- Responsible technician.
- Intake condition and accessories.
- Approved/pre-authorized scope.

Technician captures:

- Diagnosis.
- Parts demanded and consumed.
- Labor/time.
- Warranty determination.
- Cause/remedy.
- Testing result.
- Completion meter reading.
- Final condition/photos.

### 7.3 Hybrid

Use only when a job genuinely requires both on-site and shop execution.

Example:

1. Field technician diagnoses on-site.
2. Unit or component is transported to the shop.
3. Repair Order performs shop work and consumes components.
4. Field Service task returns for installation/startup.

Rules:

- One Service Case coordinates both.
- Each execution record retains its native status and inventory/time.
- One primary quotation covers the approved scope.
- Additional work follows the same change-order rules.
- Case completion requires all non-cancelled execution records to be complete.

## 8. Estimating and quotation synchronization

### 8.1 Estimate sources

Technical estimates may originate from:

- Repair component demand.
- Planned Field Service time/materials.
- Service Advisor-defined scope.
- Standard Service quotation templates.

### 8.2 Creating the quotation

**Create Quotation**:

1. Checks for an existing active primary quotation.
2. Uses the Service quote type.
3. Copies customer and Client Equipment.
4. Adds approved estimate inputs as Sales order lines.
5. Adds sections/notes identifying equipment and requested work.
6. Stores immutable source references on generated lines.
7. Opens the draft for Service Advisor review.

### 8.3 Updating a draft

Before a quotation is sent:

- **Refresh Estimate** may update source-generated lines.
- Manually added lines are preserved.
- Removed technical estimate items are marked for review rather than silently
  deleted.

After a quotation is sent or approved:

- The system never silently rewrites customer-visible scope.
- Additional work creates a change-order quotation/revision.
- The original approval remains auditable.

### 8.4 Fixed price versus time and material

Fixed price:

- Customer price remains the approved amount.
- Actual time/materials feed internal margin reporting.
- Overrun creates an internal exception unless customer scope changed.

Time and material:

- Approved actual quantities may update invoiceable Sales lines according to
  configured policy.
- Unapproved additional scope still requires a change order.

Warranty/no-charge:

- Requires reason and responsible approval.
- Technical time/material cost remains recorded.
- Customer-facing zero-charge lines remain traceable when appropriate.

## 9. Parts, Inventory, and Purchase process

### 9.1 Common Parts Request

Create `southern.service.part.request` as a shared demand record linked to one
source:

- Service Case.
- Field Service task.
- Repair Order.
- Maintenance Request.

Fields:

- Source work/domain.
- Product.
- Requested quantity/UoM.
- Needed-by date.
- Requesting technician.
- Demand type.
- Stock status.
- Preferred/selected vendor.
- Buyer.
- Linked PO line(s).
- Receipt/issue status.
- Blocking/non-blocking flag.

Statuses:

- Draft.
- Submitted.
- Stock Available.
- Sourcing.
- RFQ.
- Ordered.
- Partially Received.
- Received.
- Issued/Consumed.
- Cancelled.

### 9.2 Stocked part

1. Technician/advisor adds a required part.
2. Odoo checks forecast and source warehouse/location.
3. If available:
   - Field Service uses its native material/Sales line and stock behavior.
   - Repair uses native component reservation/consumption.
   - Maintenance creates the approved internal issue/consumption record.
4. Parts Request shows **Stock Available**.
5. Warehouse issues/reserves the part.
6. Work is not marked Waiting for Parts unless the part is blocking and not
   reserved.

### 9.3 Customer-demand/MTO part

1. A configured Sales line triggers native MTO/Buy procurement.
2. Odoo retains the native Sales line → Purchase line relationship.
3. The Parts Request reads that procurement relationship rather than creating a
   duplicate PO.
4. Buyer receives the RFQ in Purchase.
5. Confirmed PO and expected receipt update the Service Case.

### 9.4 Manually requested service part

Use when no native Sales procurement link exists:

1. Technician submits Parts Request.
2. Buyer reviews stock, alternatives, source, quantity, vendor, cost, and
   needed-by date.
3. Buyer either:
   - Fulfills from stock.
   - Adds to an existing compatible draft RFQ.
   - Creates a new RFQ.
   - Rejects/returns the request with reason.
4. PO line stores demand type and exact source work link.
5. Confirmation changes request to **Ordered**.
6. Expected receipt is visible to advisor, dispatcher, and technician.
7. Late expected receipt creates an activity for Buyer and Dispatcher.
8. Receipt changes request to **Received**.
9. If all blocking parts are available, Odoo moves the work from
   **Waiting for Parts** to **Ready** and alerts the owner.

### 9.5 Normal stock replenishment

Normal reorder-rule replenishment:

- Does not pretend to originate from a customer case.
- Uses demand type **Stock**.
- May satisfy many future jobs.
- Remains visible in Purchase/Inventory but not as a dedicated customer-demand
  link.

### 9.6 Purchase controls

Before PO confirmation:

- Buyer required.
- Vendor required.
- Expected receipt required.
- Every line classified as Stock, Sales, Field Service, Repair, or Maintenance.
- Non-stock-demand lines require a source reference.

Vendor bills:

- Purchase control policy and receipts determine bill readiness.
- Billing exceptions remain in Accounting/Purchase queues.
- Customer Ready-to-Invoice is not automatically blocked by vendor billing
  unless company policy explicitly requires it.

## 10. Additional work and change orders

1. Technician records additional finding.
2. Adds proposed labor/parts and photos.
3. Marks whether work must stop.
4. Case enters **Waiting for Customer** if authorization is required.
5. Service Advisor reviews and creates change-order quotation.
6. Customer approves/signs/pays as configured.
7. Approved change-order lines link to their source finding.
8. Work returns to **In Progress** or **Waiting for Parts**.
9. Declined work remains recorded with decline reason and recommended follow-up.

Emergency exception:

- Authorized manager may approve proceed-before-signature.
- Requires reason, authorization source, name, timestamp, and limit.
- The system creates a follow-up activity to obtain written confirmation.

## 11. Completion and invoice process

### 11.1 Technical completion

Field Service completion requires:

- Time entry.
- Materials.
- Findings/cause/remedy.
- Meter/run hours when applicable.
- Worksheet/photos.
- Customer signature or exception.

Repair completion requires:

- Actual components consumed.
- Labor/time.
- Findings/cause/remedy.
- Test result.
- Final condition.
- Return/pickup readiness.

Maintenance completion requires:

- Time and internal parts.
- Failure/cause/remedy.
- Meter reading when applicable.
- Preventive next-due update.

### 11.2 Commercial review

For customer work:

1. System compares approved scope with actual time/materials.
2. Flags unapproved additions, missing quantities, or incomplete commercial
   links.
3. Service Advisor resolves exceptions.
4. Case moves to **Ready to Invoice**.

### 11.3 Invoice

1. Billing opens Ready to Invoice.
2. Reviews Sales Order, delivered/completed quantities, customer authorization,
   tax, payment terms, and invoice address.
3. Creates/posts invoice using native Sales/Accounting flow.
4. Case displays invoice status.
5. Operational closure may occur after invoicing or after payment according to
   company policy.

## 12. Internal Maintenance process

### 12.1 Preventive

1. Maintenance schedule identifies due internal equipment.
2. System creates/suggests Maintenance Request.
3. Maintenance Planner reviews equipment, meter/date trigger, scope, team,
   planned date, and required parts.
4. Request enters **Ready**, **Scheduled**, or **Waiting for Parts**.
5. Technician performs work and records time, parts, reading, findings, and
   remedy.
6. Completion calculates the next due date/meter.
7. No Sales quotation, customer authorization, or invoice is created.

### 12.2 Corrective

1. Employee/manager opens New Maintenance Request.
2. Selects internal equipment.
3. Records failure, severity, downtime, and safety impact.
4. Planner assigns team/technician and schedule.
5. Parts follow the shared Parts Request/Purchase process.
6. Technician records cause, remedy, time, parts, and test result.
7. Equipment history and downtime metrics update.

### 12.3 Conversion boundary

If work is actually for customer-owned equipment:

- Do not continue in Maintenance.
- Create Customer Service and link/cancel the mistaken Maintenance Request with
  reason.

If customer equipment becomes company-owned:

- Use a controlled ownership-conversion process.
- Create a new Maintenance Equipment record.
- Preserve cross-reference and historical customer work.

## 13. Equipment handling

### 13.1 Selecting equipment

After selecting customer:

- Equipment list is limited to that commercial entity.
- Search supports serial, model, manufacturer, name, and site.
- Retired equipment is hidden by default.

### 13.2 New equipment

Minimum before active use:

- Customer.
- Equipment name/category.
- Manufacturer/model when known.
- Serial or approved unserialized indicator.
- Site/service address.

### 13.3 Unserialized equipment

Never create fake serials such as `N/A`, `UNKNOWN`, or repeated zeros.

Use:

- Explicit unserialized flag.
- Equipment description/model.
- Customer/site.
- Optional customer asset number.
- Manager-reviewed exception where required.

### 13.4 Duplicate equipment

Potential duplicate rules:

- Exact normalized serial.
- Same customer + manufacturer + model + customer asset number.
- Conflicting customer ownership on identical serial.

Duplicates enter review. The system does not automatically merge equipment,
history, or ownership.

## 14. Daily role-based work

### Service Advisor

- New service intake.
- Quotes awaiting preparation.
- Quotes waiting for customer.
- Change orders.
- Completed work needing commercial review.
- Customer pickup/return decisions.

### Dispatcher

- Ready but unscheduled.
- Unassigned.
- Overdue.
- Waiting for Parts with expected dates.
- Parts Received / Work Ready.
- Technician capacity.

### Technician

- My Work Today.
- Scheduled next.
- Waiting for my input.
- Additional work awaiting approval.
- Parts received for my work.

### Shop Manager

- Intake/diagnosis queue.
- Approved shop work.
- Repair parts availability.
- In-progress aging.
- Test/closeout exceptions.

### Maintenance Planner

- Preventive due/overdue.
- Corrective priority/downtime.
- Internal work waiting for parts.
- Maintenance technician schedule.

### Buyer

- Submitted parts requests.
- RFQs without buyer.
- POs without source.
- Late expected receipts.
- Partially received blocking demand.

### Billing

- Ready to Invoice.
- Completed but commercially blocked.
- Sales Orders waiting on delivered quantities.
- Invoice exceptions.

### Operations Manager

- Cross-domain backlog.
- Service cycle time.
- Waiting-time aging.
- Quote conversion.
- First-time completion/repeat work.
- Technician utilization.
- Parts availability and purchasing lead time.
- Completion-to-invoice lag.

## 15. Notifications and activities

Create activities, not uncontrolled email noise:

| Event | Owner |
|---|---|
| New unassigned service case | Service Manager |
| Ready on-site work without schedule | Dispatcher |
| Quote sent and nearing expiration | Service Advisor |
| Customer approval received | Service Advisor and Dispatcher/Shop Manager |
| Blocking part PO confirmed | Source work owner |
| Blocking part late | Buyer and Dispatcher/Shop Manager |
| All blocking parts received | Source work owner |
| Additional work identified | Service Advisor |
| Work completed with commercial exception | Service Advisor |
| Ready to Invoice | Billing |
| Preventive maintenance due | Maintenance Planner |

Activities close automatically only when their corresponding business condition
is resolved.

## 16. Exception rules

| Exception | System response |
|---|---|
| Customer has no Client Equipment record | Allow draft intake; require create/link or approved exception before planning |
| Equipment belongs to another customer | Block selection; manager-controlled ownership review |
| Missing/placeholder serial | Use unserialized workflow or data review; never fabricate |
| Work starts before quote | Require pre-authorization/warranty/emergency reason |
| Additional unapproved work | Stop affected work and create change order |
| Part unavailable | Create/link Parts Request and Waiting for Parts |
| PO has no buyer/source | Block controlled confirmation |
| Work complete but time/material missing | Block technical completion |
| Work complete but no Sales link | Route to commercial exception queue |
| Customer declines | Record declined scope and disposition; do not delete diagnosis |
| Duplicate active Service Case | Warn and link/open existing case |

## 17. Reporting definitions

Use consistent timestamps:

- Intake at.
- Diagnosis completed at.
- Quote sent at.
- Customer approved at.
- Ready at.
- Scheduled at.
- Started at.
- Work completed at.
- Ready to invoice at.
- Invoiced at.
- Closed at.

Core measures:

- Intake-to-quote time.
- Quote-to-approval time.
- Approval-to-start time.
- Active work time.
- Waiting-for-parts time.
- Waiting-for-customer time.
- Work-complete-to-invoice time.
- Total cycle time.
- Quote conversion rate.
- Repeat work by Client Equipment.
- First-time completion rate.
- Estimated versus actual labor/material.
- Gross margin by service type, restricted by role.
- PO lead time and on-time receipt for blocking demand.
- Preventive maintenance compliance.
- Internal equipment downtime.

## 18. Detailed examples

### Example A — On-site hydraulic leak

1. Customer calls.
2. Advisor creates Customer Service Case and selects excavator by serial.
3. Work location is On-site; estimate is required.
4. Diagnostic FSM task is created.
5. Technician diagnoses hose and fitting, records time/photo, and proposes
   parts.
6. Advisor creates linked Service quotation.
7. Customer signs.
8. One part is stocked; one is MTO.
9. Case becomes Waiting for Parts.
10. Buyer confirms linked PO; expected date appears on case.
11. Receipt changes case to Ready and alerts Dispatcher.
12. Dispatcher schedules return visit.
13. Technician completes work and obtains signature.
14. Advisor reviews fixed-price versus actual cost.
15. Case becomes Ready to Invoice.
16. Billing invoices from Sales.

### Example B — Shop repair with additional damage

1. Customer drops off unit.
2. Advisor creates case, equipment, intake condition, and accessories.
3. Shop Repair Order is created.
4. Technician diagnoses quoted repair.
5. Customer approves Service quotation.
6. During teardown, technician finds additional damage.
7. Case moves to Waiting for Customer.
8. Advisor sends change order with photos.
9. Customer approves.
10. Buyer orders blocking component through linked Parts Request.
11. Part receipt returns repair to Ready/In Progress.
12. Technician finishes, tests, and records actual components.
13. Advisor closes commercial exception review.
14. Billing invoices; customer pickup activity is completed.

### Example C — Internal preventive maintenance

1. Loader reaches its meter interval.
2. Preventive Maintenance Request is generated.
3. Planner schedules internal technician and checks parts.
4. Filter is unavailable; Parts Request goes to Buyer.
5. Receipt alerts Maintenance Planner.
6. Technician performs maintenance and records parts, time, reading, and findings.
7. Next meter/date is calculated.
8. Request closes with no Sales quotation or invoice.

## 19. Implementation sequence for this process

1. Deploy application foundation, roles, quotation types, and menus.
2. Establish Client Equipment links and read-only legacy migration.
3. Introduce Service Case and shared status.
4. Route On-site/Shop/Hybrid execution.
5. Add equipment-aware quotation creation.
6. Add Parts Request and Purchase tracking.
7. Add completion and Ready-to-Invoice controls.
8. Activate Internal Maintenance equipment and preventive schedules.
9. Retire duplicate Equipment Jobs and legacy free-text identity after verified
   migration.

## 20. Approval points

Before implementation, approve:

1. Service Case as the non-transactional coordination header.
2. Required status names and transition controls.
3. Customer signature and emergency authorization policy.
4. Fixed-price versus time-and-material policies.
5. Parts Request approval thresholds.
6. Whether operational closure occurs at invoice creation or customer payment.
7. Role assignments and visibility of cost/margin.
