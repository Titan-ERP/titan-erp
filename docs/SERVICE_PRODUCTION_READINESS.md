# Southern Service — Production Readiness Dossier

## Release candidate

- Add-on: `southern_service_operations`
- User-facing application: **Sales**
- Candidate code commit: `2a10970`
- Odoo.sh development branch: `codex/service-development-test`
- Odoo.sh development commit: `ea7752d`
- Odoo.sh build: `35696342`
- Odoo version: 19.0 Enterprise
- Production deployment: **not performed**

The release candidate makes **Sales the single personnel workspace** without
replacing Odoo's native transaction models:

- Sales is the entry point for quotation type, customer Service intake,
  diagnosis, estimating, routing, linked work, purchasing visibility, customer
  approval, sales orders, and billing.
- `southern.service.case` is the coordination header.
- On-site work executes in Field Service (`project.task`).
- Shop work executes in Repairs (`repair.order`).
- Company-owned equipment work executes in Maintenance
  (`maintenance.request`).
- Customer equipment remains `equipment.details` and must be tied to its
  customer contact.
- Internal equipment remains `maintenance.equipment`.
- Purchase Orders remain the vendor commitment and are linked back to the
  Service Case through Purchase Order lines.

## Verification evidence

| Check | Result | Evidence |
|---|---|---|
| Odoo.sh module install/upgrade | Pass | Build `35696342` loaded the module, security, Sales/Service views, menus, and all dependencies |
| Odoo 19 transaction tests | Pass | `0 failed, 0 error(s) of 6 tests`; Sales-workspace routing, on-site idempotency, internal Maintenance routing, and Sales task reuse included |
| Standalone repository tests | Pass | 12 tests |
| Python compilation | Pass | `compileall` on `southern_service_operations` |
| XML parse and manifest file validation | Pass | Included in standalone suite |
| Odoo 19 search-view compatibility | Pass | Static regression plus live build load |
| Odoo 19 SQL constraint API | Pass | Native `models.Constraint`; legacy `_sql_constraints` warning removed |
| Sales-hosted Service menus | Pass | Live UI shows Service inside Sales with New Service, Internal Maintenance, cases, work queues, equipment, and purchasing |
| Sales quote-type actions | Pass | Parts, Service, Equipment Sale, and Rental open native Sales quotations with defaults |
| Technician Service quotation | Pass | Live UI created Sales quotation `S00031`, Service Case `SVC26-00001`, and one linked Field Work record |
| Customer equipment relationship | Pass | Equipment owner drives customer and mismatched commercial entities are blocked |
| On-site Service routing | Pass | One Field Service task; second routing action creates no duplicate |
| Sales smart-button navigation | Pass | Live `S00031` opened its linked Field Work while remaining in the Sales application |
| Sales confirmation after routing | Pass | Transaction test confirms the routed task is reused and receives Sales order/line links |
| Internal Service routing | Pass | Manual UI created one native Maintenance Request; repeated routing remained at one |
| Shop Service prerequisite | Pass | Missing equipment-to-product mapping is blocked with a clear validation message |
| Shop Service routing | Pass | After a development-only product mapping, one native Repair Order was created; repeated routing remained at one |
| Production isolation | Pass | All runtime writes were limited to disposable Odoo.sh development databases |

The Odoo.sh build is yellow because the inherited development stack emits
warnings outside this add-on, including a missing `author` manifest key in
`l10n_us_hr_payroll_ms_status`. The final Service test run emits no
Service-specific model, view, manifest, constraint, test, or traceback warning.

## Functional release scope

### Sales — the unified workspace

- Keep standard **New** for General quotations.
- Add upper-left **Parts**, **Service**, **Equipment Sale**, and **Rental**
  quotation actions.
- Place the complete Service navigation tree inside the Sales application.
- Make **New Service** open a native Sales quotation rather than a separate
  Service intake form.
- Store one quotation type on the native `sale.order`.
- Capture requested work, authorization, work location, customer, and Client
  Equipment on the Sales quotation.
- Allow authorized Service personnel to route Field Service and/or Shop Repair
  directly from the quotation before customer approval when diagnosis is
  required.
- Show the linked Service Case, Field Work, Shop Work, and Purchase tracking as
  smart buttons on the Sales document.
- Require work location and either Client Equipment or a documented exception
  before confirming a Service quotation.
- Create or reuse one linked Service Case.
- Reuse an already-routed unbilled Field Service task when a Service quotation
  is confirmed, preventing duplicate work orders.

### Service execution behind Sales

- Use Sales as the user-facing Service workspace for customer work.
- Keep the Service Case as the orchestration record behind the Sales
  quotation/order.
- Route by work location:
  - On-site → Field Service task.
  - Shop → Repair Order.
  - On-site and Shop → both native records.
  - Our Equipment → Maintenance Request.
- Make routing idempotent.
- Expose operational queues, equipment, and purchase tracking.
- Expose those queues and links under **Sales → Service**, not as a separate
  application.
- Create internal Maintenance intake under **Sales → Service → New Internal
  Maintenance** without generating a customer quotation.
- Keep the native execution record authoritative for time, inventory,
  components, and operational completion.

### Equipment

- Treat Client Equipment as the customer-owned equipment master.
- Require an owning customer contact.
- Validate the same commercial customer across Equipment, Service Case, Sales,
  Field Service, and Repair.
- Require a real serial number or the explicit **Unserialized** flag.
- Require an Odoo Product mapping before shop repair routing.
- Preserve service history with smart links to cases, scheduled work, and shop
  work.
- Keep Maintenance Equipment separate for company-owned assets.

### Purchase

- Link Purchase Order lines to the Service Case.
- Expose linked Purchase Orders from Service.
- Preserve native Sales/Purchase procurement and native Repair/Purchase
  behavior; the module does not invent a second procurement engine.

## Required approval gates

These are operational approvals, not missing code:

1. Assign named users to **Service User** or **Service Manager** and verify their
   existing Sales, Field Service, Repair, Maintenance, Purchase, and Accounting
   permissions. Service User grants access to the user's own Sales quotations.
2. Decide whether technicians may send and confirm their own quotations or
   whether those transitions require advisor/manager approval. The current
   candidate uses Odoo's native Sales User permissions.
3. Complete role-based UAT with at least one service advisor, dispatcher/shop
   coordinator, technician, maintenance user, buyer, billing user, and
   operations manager.
4. Review active Client Equipment for missing customer, serial/unserialized
   decision, duplicate serials, and missing Product mappings needed for shop
   work.
5. Confirm the production Field Service project that should receive on-site
   Service tasks.
6. Confirm quotation templates, service products, invoicing policy, warehouses,
   repair locations, and purchasing routes.
7. Approve a production change window, backup owner, smoke-test owner, and
   rollback decision maker.

## Deployment runbook — do not execute without approval

1. Freeze the candidate commit and create a production release branch containing
   only the Service add-on commits. Do not include development-only copies of
   prerequisite modules.
2. Refresh staging from a current production backup.
3. Verify production/staging contain compatible installed versions of
   `cs_client_equipment`, `dmc_fieldservice`, Sales, Field Service, Repairs,
   Maintenance, Purchase, and their declared dependencies.
4. Install or upgrade `southern_service_operations` in staging.
5. Run:
   - the 12 standalone repository checks;
   - the Odoo transaction suite (currently 6 tests reported by Odoo);
   - the role-based UAT scenarios in this dossier;
   - a dry-run equipment/readiness audit.
6. Resolve equipment exceptions and approve the user-role mapping.
7. Take and verify the production backup immediately before the change window.
8. Deploy the exact staging-tested commit and install/upgrade only
   `southern_service_operations`.
9. Perform production smoke tests without creating unnecessary live customer
   transactions:
   - Sales → Service menu access;
   - Sales quote-type buttons;
   - one approved Service quotation with customer/equipment intake;
   - Service Work tab and linked-work smart buttons;
   - one controlled on-site or shop routing test;
   - linked Sales/Service/equipment navigation.
10. Monitor module errors, failed validations, duplicate work records, waiting
    queues, and user feedback daily for the first week.

## Rollback plan

- Before module installation: stop and leave production unchanged.
- If installation fails before users create Service records: restore the
  pre-change backup or deploy a forward fix after reviewing the failure.
- If users have created Service records: do not uninstall the module as the
  default rollback. Remove Service access, stop new intake, preserve records,
  and deploy a forward fix.
- Restore the pre-change backup only when the business accepts losing all
  transactions entered after that backup.
- Retain the candidate commit, build logs, test output, database backup ID,
  deployment timestamp, and approver names with the change record.

## Go/no-go position

The code candidate is ready for a current-production-copy staging/UAT cycle.
Production remains a **no-go** until the approval gates above are signed off.
