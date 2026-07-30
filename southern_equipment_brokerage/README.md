# Southern Equipment Brokerage

Odoo 19 addon for Southern Equipment's broker-assisted sourced equipment workflow.

## Included

- Sourced listings with separate public and restricted source/seller fields
- Public opportunity catalog and detail pages at `/equipment-opportunities`
- Dynamic homepage section showing up to three currently published opportunities
- Buyer inquiry intake with partner matching, CRM opportunity creation, and broker call activity
- Brokered deal, inspection, contract/assignment, and manual deposit-ledger workflows
- Comparable equipment records and deal math
- Explicit Admin, Deal Broker, and Inspector Coordinator roles
- Chatter and activities on listings, inquiries, deals, inspections, and assignments

No public user receives model access. Website controllers fetch only records explicitly marked
`website_published`, and every displayed value is selected from the public-safe field set.
Sensitive source, seller, serial, margin, contract, and deposit fields also carry field-level
group restrictions.

## Install

Place `southern_equipment_brokerage` on the Odoo 19 addons path, update the Apps list, and
install **Southern Equipment Brokerage**. Assign users under **Settings → Users**:

- Southern Equipment Admin
- Deal Broker
- Inspector Coordinator

The module intentionally does not capture payments, implement escrow, create legal documents,
or assume when seller consent is legally required. Those decisions remain explicit tracked
fields pending attorney and processor review.

## CSV import

Use **Equipment Brokerage → Pipeline → Import Facebook Agent CSV** for the current
`odoo-equipment-opportunities.csv` export. The wizard accepts its exact CRM-shaped headers
(`Opportunity`, `Customer`, `Stage`, `Expected Revenue`, `Equipment ID`, and the remaining
export columns) and maps them into the brokerage listing model.

The wizard stores seller contact, exact location, Facebook URL, internal notes, and captured
text only in restricted fields. Blank VIN/serial values remain blank. Imported records are
always unpublished with a verification-required status; the importer never copies an exact
seller location into the public region.

For other data sources, Odoo's standard **Sourced Listings → Actions → Import records** flow
can be used. A native compatible header template is provided at
`data/odoo-equipment-opportunities-template.csv`.

For the current Facebook Agent bridge, map its exported headers to the labels in the template.
Selection values should use these technical keys:

- `source`: `facebook_marketplace`, `machinerytrader`, `auctionvalues`, `vip`, `dealer`,
  `auction`, `manual`, `other`
- `public_status`: `draft`, `needs_verification`, `published`, `inquiry_received`,
  `verification_in_progress`, `seller_confirmed`, `under_negotiation`, `under_contract`,
  `assigned`, `unavailable`, `sold`, `archived`
- `equipment_type`: `skid_steer`, `dozer`, `excavator`, `mini_excavator`, `telehandler`,
  `forklift`, `tractor`, `loader`, `other`
- `comp_confidence`: `low`, `medium`, `high`
- `grade`: `strong`, `good`, `verify`, `pass`

Keep `Published on Website` false during initial import. Review the public title, description,
region, verification note, ownership flag, and photos before publishing. Never fill a missing
VIN/serial with a placeholder.
