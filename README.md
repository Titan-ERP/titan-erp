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

See [Odoo-native ownership](docs/ODOO_NATIVE_SYSTEMS.md) and the
[production runbook](docs/ODOO_PRODUCTION_RUNBOOK.md).
