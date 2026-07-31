# titan-erp

Titan ERP Odoo addons and controlled automation workflows.

The local automation is connected to live Odoo through an authenticated,
retry-aware shared client. Reads cover the full dataset. Writes remain dry-run
unless every supervised apply control is satisfied.

See [docs/OPERATIONS_CONTROL.md](docs/OPERATIONS_CONTROL.md) for the runtime,
quality queues, dashboard, artifact contracts, and deployment controls.
