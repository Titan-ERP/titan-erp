# Southern Equipment Brokerage Production Checklist

Complete this checklist in staging before merging the production-candidate branch.

## Release gate

- Confirm the Odoo.sh build completes without module or test failures.
- Take and verify a production database backup before installation.
- Install or upgrade `southern_equipment_brokerage` in staging first.
- Assign only approved users to Southern Equipment Admin, Deal Broker, or
  Inspector Coordinator.
- Confirm Inspector Coordinator cannot open buyer inquiries, seller/source
  fields, contracts, or deposit details.
- Confirm the production company and allowed-company settings are correct for
  every brokerage user.

## Website and privacy

- Replace the default website name, logo, phone, email, address, and footer copy.
- Publish an attorney-reviewed Privacy Policy at `/privacy`.
- Set `southern_equipment_brokerage.privacy_notice_version` in System
  Parameters whenever the approved privacy notice changes.
- Review the broker-assisted opportunity disclosure, deposit language,
  inspection authorization, contract assignment, fees, refunds, and consent
  wording with counsel. The addon tracks workflow and does not create legal terms.
- Test a public inquiry from a logged-out browser and verify consent timestamp,
  notice version, CRM opportunity, broker activity, and duplicate suppression.
- Configure an approved retention policy for buyer inquiries, CRM records,
  contracts, inspection reports, and source/seller data.
- Confirm public pages never display source URLs, exact seller locations, seller
  names or contact data, internal notes, VIN/serial unless explicitly approved,
  margin, spread, deposits, or contracts.

## Listings and media

- Import into staging with **Validate Only** selected first.
- Reconcile validation counts and review every rejected or skipped row.
- Keep imported records unpublished with Verification in Progress.
- Confirm every published listing has a reviewed public title, general region,
  verification note, broker-assisted disclosure, and primary image.
- Publish only owned, licensed, dealer-authorized, or appropriately licensed
  generic media. Record the source/license note and confirm publication rights.
- Never reuse marketplace seller photos without documented permission.
- Never invent VIN or serial values.

## Operations

- Configure outgoing email and verify broker activity notifications.
- Confirm the fallback Deal Broker assignment is staffed and active.
- Test deposit ledger posting, overdraw prevention, voiding, inspection
  completion, contract execution, seller consent when required, assignment, and
  closeout with non-production data.
- Confirm deposits and payment capture remain manual until the legal structure,
  processor, refund policy, and accounting treatment are approved.
- Do not enable automated seller messages, bids, purchases, or public bidding.

## Deployment and rollback

- Record the tested commit, Odoo.sh build number, module version, test results,
  database backup time, release owner, and approver.
- Schedule a short monitored release window.
- After production installation, smoke-test the backend app, public catalog,
  unpublished 404 behavior, inquiry form, CRM creation, and access groups.
- If installation or smoke tests fail, stop imports and publishing, restore the
  pre-release backup or revert the release commit, and document the incident.
