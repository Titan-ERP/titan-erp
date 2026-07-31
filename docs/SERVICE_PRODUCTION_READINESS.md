# Southern Service — Production Readiness Dossier

## Release candidate

- Add-on: `southern_service_operations`
- User-facing application: **Sales**
- Code candidate commit: `b2e5fd2825e80af7936c4b7bf0e6184a0489ad20`
- Odoo.sh development branch: `codex/southern-service-production-rc`
- Odoo.sh development build: `35738421`
- Odoo.sh production-copy staging build: `35740172`
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
| Odoo.sh module install/upgrade | Pass | Build `35738421` completed and the module loaded on a fresh Odoo 19 development database |
| Odoo 19 transaction tests | Pass | 13 add-on transaction scenarios loaded without a Service-specific test failure |
| Standalone repository tests | Pass | 25 tests, including additive/non-destructive installation coverage |
| Python compilation | Pass | `compileall` on `southern_service_operations` |
| XML parse and manifest file validation | Pass | Included in standalone suite |
| Odoo 19 search-view compatibility | Pass | Static regression plus live build load |
| Odoo 19 SQL constraint API | Pass | Native `models.Constraint`; legacy `_sql_constraints` warning removed |
| Sales-hosted Service menus | Pass | Live build exposes Service under Sales with no separate Southern Service app root |
| Sales quote-type actions | Pass | Parts, Service, Equipment Sale, and Rental open native Sales quotations with defaults |
| Technician Service quotation | Pass | Live build opens the Service Jobs landing page and one combined Service Job / native quotation workspace |
| Customer equipment relationship | Pass | Equipment owner drives customer and mismatched commercial entities are blocked |
| On-site Service routing | Pass | One Field Service task; second routing action creates no duplicate |
| Sales confirmation after routing | Pass | Transaction test confirms the routed task is reused and receives Sales order/line links |
| Internal Service routing | Pass | Manual UI created one native Maintenance Request; repeated routing remained at one |
| Shop Service prerequisite | Pass | Missing equipment-to-product mapping is blocked with a clear validation message |
| Shop Service routing | Pass | After a development-only product mapping, one native Repair Order was created; repeated routing remained at one |
| Production isolation | Pass | All runtime writes were limited to disposable Odoo.sh development databases |

## Production-copy staging evidence

Build `35740172` completed successfully after the isolated release candidate was
promoted to Odoo.sh Staging. Odoo.sh copied the current production database,
caught outbound email, and disabled scheduled actions for user-acceptance
testing. The new add-on was then discovered with **Update Apps List** and
activated explicitly; new add-ons are not installed merely because their code
is present on a staging or production branch.

The controlled staging scenario created no production records and produced the
following evidence:

| Check | Result | Evidence |
|---|---|---|
| Staging build | Pass | Build `35740172`, Odoo 19.0, `Test: Success` |
| Existing labor product mapping | Pass | Existing production product `[LABOR-SHOP] Shop Labor Rate`, service type, $150.00/hour, 6% sales tax |
| Combined Service intake | Pass | Task `118`, `PRE-MERGE UAT - Hydraulic pressure loss`, created from **Sales > Service** |
| Client equipment ownership | Pass | `Southern UAT Excavator`, serial `PREMERGE-UAT-20260731`, 2,450 hours, owner `Henry Campbell` |
| Equipment service history | Pass | Equipment record links Southern Service case `SVC26-00001`; no duplicate equipment was created |
| Native Sales quotation | Pass | `S00192`, status Quotation, customer/equipment/service job linked |
| Labor-to-quote flow | Pass | One 2.50-hour task generated one `[LABOR-SHOP]` line at $150.00/hour |
| Production product selection | Pass | Existing product `[07000-B1009] O-Ring - 07000-B1009`, quantity 2.00, $3.49/unit |
| Quotation totals | Pass | $381.98 untaxed, $22.92 tax, $404.90 total; quotation remained unconfirmed and unsent |
| Digital equipment inspection | Pass | Completed with one High / Repair Needed hydraulic finding, measurement, fault `H-214`, and AI-context flag |
| Mobile photo intake | Pass | **Add Service Photo** opens a job- and equipment-linked photo record with camera guidance and AI-context control |
| Missing-AI-key safeguard | Pass | Estimate request is blocked with an admin-directed configuration message; no partial suggestion is applied |
| Live AI reviewed estimate | Pass | `gpt-5.6-sol` produced a medium-confidence, Needs Review draft with three diagnostic tasks totaling 5.00 hours, no unverified parts, and an editable customer quotation note |
| AI transaction safety | Pass | Review was saved without applying it; quotation `S00192` remained unconfirmed, unsent, and unchanged at $404.90 |

The live AI gate passed with the dedicated service-account key in staging. The
assistant used the complaint, equipment hours, technician findings, and the
completed high-priority hydraulic inspection. It proposed diagnosis only and
correctly returned no part suggestion because no component failure or exact
catalog match was established. Equipment-specific manual and H-214 references
remain an explicit technician question until an approved manual vector store is
configured. The key must not be copied into this dossier, source control,
chatter, screenshots, or test output.

## Record-preserving deployment guarantees

- Install the add-on into the existing production database. Never replace
  production with the staging database or import the staging UAT records.
- Preserve every existing customer, contact, Sales quotation/order, invoice,
  Product, Client Equipment record, Field Service task, Repair Order,
  Maintenance record, Purchase Order, attachment, and chatter entry.
- The add-on is additive: it adds nullable links, Service coordination models,
  views, menus, and security. It has no pre-install, post-install, or uninstall
  hook; no migration or upgrade script; and no raw delete, truncate, table-drop,
  or column-drop operation.
- Do not uninstall existing Client Equipment, Field Service, Repair,
  Maintenance, Sales, Purchase, Inventory, or Accounting modules as part of
  this deployment.
- Do not deduplicate, resequence, archive, rewrite, or bulk-update existing
  records during installation. Any future cleanup requires its own backup,
  dry-run report, approval, and reversible change plan.
- Service-generated draft quotation lines may only be removed through an
  explicit user change to that Service scope. Confirmed Sales Order lines are
  protected from Service-side removal.
- Verify the pre-change production backup and restore controls immediately
  before deployment. A staging database copy validates behavior but is not a
  substitute for the production backup.

The Odoo.sh build is yellow because the inherited production stack emits
warnings outside this add-on: legacy Client Equipment access-record creation
messages and an accessibility warning in the existing company setup wizard.
Build `35738421` emits no Southern Service model, view, manifest, constraint,
test, or traceback warning.

## Functional release scope

### Sales — the unified workspace

- Keep standard **New** for General quotations.
- Add upper-left **Parts**, **Service**, **Equipment Sale**, and **Rental**
  quotation actions.
- Place the complete Service navigation tree inside the Sales application.
- Make **Service** open the Service Jobs landing page inside Sales. **New** opens
  the combined Service Job page where tasks and the native Sales quotation are
  built together.
- Store one quotation type on the native `sale.order`.
- Capture requested work, authorization, work location, customer, and Client
  Equipment on the Sales quotation.
- Allow authorized Service personnel to route Field Service and/or Shop Repair
  directly from the quotation before customer approval when diagnosis is
  required.
- Show the linked Service Case, Field Work, Shop Work, and Purchase tracking as
  smart buttons on the Sales document.
- Require work location and a customer-owned Equipment record. Serial-number
  intake may create or reuse that customer-linked record before confirmation.
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

### AI Estimate Assistant

- Use the OpenAI Responses API only from the Odoo server; the API key is never
  sent to a technician's browser or stored in source control.
- Prefer the `OPENAI_API_KEY` server environment variable in production. When
  Odoo.sh does not expose a supported persistent environment-secret mechanism,
  enter the dedicated production service-account key only in the admin-only
  Southern Service setting. Never commit it to Git or an `.env` file.
- Generate structured, reviewable suggestions from the customer complaint,
  equipment identity and hours, technician findings, approved manuals, and a
  bounded Odoo product catalog.
- Keep customer symptoms distinct from AI suggestions and technician-confirmed
  findings.
- Require a technician to select every accepted task and map every accepted
  part to a real Odoo Product before applying it.
- Apply accepted labor to Service Tasks, accepted parts to native product-backed
  Sales quotation lines, and approved wording to a native quotation note line.
- Create no products during module installation. Map Shop Labor to an existing
  production service product; map AI parts only by exact production product
  code, with technician review required when no exact match exists.
- Block billable labor from reaching a quotation while the mapped production
  labor product has a zero sales rate.
- Never let AI send, confirm, purchase, deliver, invoice, or silently modify a
  quotation.
- Use `store: false` for Responses API requests and avoid sending customer
  contact details, payment data, or unrelated chatter.
- Treat manual and service-bulletin retrieval as optional until an approved
  OpenAI vector store is configured and its document ownership is established.

## Required approval gates

These are operational approvals, not missing code:

Approved production-copy staging role mapping (2026-07-31):

- **Cross** — Service Manager.
- **Raymy** — Service Manager.
- **Service** — Service User and Field Service User.

All three assignments were saved and read back in Odoo.sh staging build
`35740172`. Production user records remain unchanged pending deployment and
role-based sign-off.

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
4. In developer mode, run **Apps > Update Apps List**, locate **Southern
   Service** (`southern_service_operations`), and activate it in staging. For a
   later upgrade, upgrade only this add-on. Do not assume a new add-on is
   installed when its code arrives on the branch.
5. Run:
   - the 17 standalone repository checks;
   - the 7 Odoo transaction tests;
   - the role-based UAT scenarios in this dossier;
   - a dry-run equipment/readiness audit.
6. Resolve missing or duplicate equipment identities and approve the user-role
   mapping.
7. In **Settings > Southern Service**, map **Default Service Labor Product** to
   the existing production Shop Labor product and confirm its approved sales
   rate is nonzero. Do not create a duplicate labor product.
8. Take and verify the production backup immediately before the change window.
9. Deploy the exact staging-tested commit and install/upgrade only
   `southern_service_operations` in the existing production database. Do not
   restore, import, or copy the staging database into production.
10. Enter the dedicated production OpenAI service-account key through the
    admin-only Southern Service setting unless the approved server environment
    secret is already present. Save, generate one controlled AI review, then
    verify the key is masked and never appears in chatter or logs.
11. Perform production smoke tests without creating unnecessary live customer
   transactions:
   - Sales → Service menu access;
   - Sales quote-type buttons;
   - one approved Service quotation with customer/equipment intake;
   - Service Work tab and linked-work smart buttons;
   - one controlled on-site or shop routing test;
   - linked Sales/Service/equipment navigation.
12. Monitor module errors, failed validations, duplicate work records, waiting
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

The isolated code candidate, core workflow, record-preservation checks, and
controlled live AI review have passed a current-production-copy staging/UAT
cycle. Production remains a **no-go** until the named operational role,
change-window, smoke-test, and rollback approval gates above are signed off.
