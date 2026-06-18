# titan-erp

Custom Odoo 19 modules for the TITAN ERP platform, developed and maintained by **DMC Strategic IT**.

---

## Modules

### `dmc_company_setup_wizard`

A multi-step wizard that automates the full accounting setup for a new company in a multi-company Odoo environment. Eliminates the manual effort of configuring chart of accounts, taxes, journals, and payment providers for each new entity.

**Key features:**
- Creates a new company with address, logo, currency, and optional parent company
- Auto-generates the next sequential Bank/Cash account code
- Maps all existing shared Chart of Accounts to the new company (no duplicate records)
- Copies tax groups and taxes from a selected source company
- Creates standard journals: Sales, Purchase, Bank, Miscellaneous
- Creates default payment providers: Wire Transfer, Cash on Delivery, Demo
- Full rollback on any error — the database is never left in a partial state
- Accessible via **Settings → Technical → Company Setup Wizard** (debug mode only)

**Dependencies:** `account`, `payment`, `base_setup`

---

## Development

### Requirements

- Odoo 19.0
- PostgreSQL 14+

### Repository Structure

```
titan-erp/
└── dmc_company_setup_wizard/
    ├── __init__.py
    ├── __manifest__.py
    ├── security/
    │   └── ir.model.access.csv
    ├── static/
    │   └── description/
    │       ├── icon.png
    │       └── index.html
    └── wizard/
        ├── __init__.py
        ├── company_setup_wizard.py
        └── company_setup_wizard_views.xml
```

### Installation

1. Copy the module directory into your Odoo addons path
2. Update the module list: **Settings → Apps → Update Apps List**
3. Search for "DMC Company Setup Wizard" and install
4. Enable debug mode to access the wizard via **Settings → Technical → Company Setup Wizard**

### Updating

After pulling changes, update the module in Odoo:

```bash
# From the Odoo server
./odoo-bin -u dmc_company_setup_wizard -d <database>
```

Or via the UI: **Settings → Apps → DMC Company Setup Wizard → Upgrade**

---

## Contributing

Branch naming: `features/<description>` or `fix/<description>`

All changes should be tested against a multi-company database before merging to `main`.

---

## Author

**DMC Strategic IT**
