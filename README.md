# Titan ERP

Odoo 19 addons and controlled automation for Southern Equipment Company.
Odoo is the system of record for workflow state, approvals, product quality,
CRM classification, contact matching, accounting controls, and daily
operations health. Shop Boss is retired and is not an active dependency.

## Local validation

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,aws]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v
.\.venv\Scripts\ruff.exe check scripts tests --select E4,E7,E9,F,I
```

Copy `odoo_connection.env.example` to the ignored
`odoo_connection.env` only when a local integration is required. Never commit
API keys or AWS credentials. New external integrations use Odoo JSON-2; XML-RPC
is available only behind an explicit temporary legacy flag.

For the local Cursor handoff workspace, ignored `.env`, `.env.local`, and
`odoo_connection.env` files are provisioned from the existing secured
credential sources. Agents may load them locally but must never display or
commit their values. AWS access continues through a named AWS CLI profile; AWS
access keys do not belong in dotenv files. Environment availability does not
authorize production writes.

The ignored local `.env` may also contain the approved Stripe provider,
webhook, Terminal reader, and location variables for read-only diagnostics and
explicitly authorized payment workflows. Live Stripe values must never be
committed or printed.

See [Odoo-native ownership](docs/ODOO_NATIVE_SYSTEMS.md) and the
[production runbook](docs/ODOO_PRODUCTION_RUNBOOK.md).

See [Sparex sourcing control](docs/SPAREX_SOURCING_CONTROL.md) for the
supplier-cost queue, evidence contract, retry policy, publication gate, and
supervised ten-product recovery workflow.

See [Sparex catalog discovery](docs/SPAREX_CATALOG_DISCOVERY.md) for the
throttled listing-page inventory, resumable cursor, Odoo match queue, and the
intentional prohibition on automatic product creation.
