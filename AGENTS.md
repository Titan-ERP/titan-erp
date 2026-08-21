# AGENTS.md

## Cursor Cloud specific instructions

### What this repo is (scope)
This repository is **not a standalone runnable application**. It is a monorepo of
**Odoo 19 Enterprise add-on modules** (`southern_*`, `cs_*`, `dmc_*`,
`l10n_us_hr_payroll_ms_status`) plus a **Python automation/control-plane toolkit**
(`scripts/`, importable package `odoo_runtime`). Odoo itself is the system of
record and is hosted externally on Odoo.sh; the Odoo server and its Enterprise
core are **not** vendored here.

Because of that, work in the cloud VM is scoped to what actually runs here:
- The Python automation toolkit under `scripts/` and its `odoo_runtime` package.
- The standalone regression suite in the top-level `tests/` directory.
- The static validation flow (compile, lint, XML parse) mirrored from CI.

Full end-to-end product testing of the Odoo UI/modules requires a live Odoo 19
**Enterprise** instance + PostgreSQL (Odoo.sh dev/staging). That cannot be stood
up in the cloud VM, so do not attempt to boot Odoo here.

### Environment / how to run things
- The update script provisions a Python 3.12 virtualenv at **`.venv`** with the
  `dev,aws,agents` extras. Use it directly: `.venv/bin/python`, `.venv/bin/ruff`,
  `.venv/bin/pytest`. There is no `dev`/`serve` command.
- The authoritative command list is the CI workflow
  `.github/workflows/validate.yml` (compile → unittest → ruff → XML parse → CLI
  `--help`). Match it. README `## Local validation` shows the Windows/PowerShell
  equivalent.
- Lint must use the same rule subset as CI or you will see spurious diffs:
  `ruff check scripts tests --select E4,E7,E9,F,I`.
- Run tests with `.venv/bin/python -m unittest discover -s tests -p "test_*.py"`
  (or `.venv/bin/pytest`, which is pinned to `tests/` via `pyproject.toml`).

### Non-obvious gotchas
- **Only the top-level `tests/` directory is service-free.** Those tests mock
  Odoo/AWS/OpenAI and need no external services. The per-module `tests/` folders
  (e.g. `southern_service_operations/tests/`) are Odoo-runtime tests that require
  a live Odoo and are **not** collected by CI's `unittest discover -s tests` —
  don't expect them to pass standalone.
- **Production CLI entrypoints connect to Odoo immediately.** Scripts such as
  `scripts/odoo_record_product_automation_run.py` call
  `OdooClient(OdooConfig.from_env(...)).connect()` before doing any work, so
  without a real `odoo_connection.env` (`ODOO_URL`/`ODOO_DB`/`ODOO_API_KEY`) they
  cannot run beyond `--help`. Copy `odoo_connection.env.example` only when a live
  integration is genuinely needed; never commit it.
- **Supervised write gate.** All Odoo writes go through `odoo_runtime.ApplyGate`
  and are read-only unless every control is satisfied: `ODOO_WRITE_ENABLED=true`
  in the env, plus `--apply --confirm <workflow> --reason <text>` within
  `--max-records`. This is intentional; do not bypass it.
- `outputs/`, `tmp/`, `dist/`, `*.env` (except `*.env.example`) and `.venv/` are
  gitignored. The automation write-audit ledger is written under
  `outputs/write_audit/`.
