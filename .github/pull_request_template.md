## Production change

- [ ] Scope is limited to the stated Odoo business process.
- [ ] Module versions were bumped for every upgraded addon.
- [ ] Standalone tests, Python compilation, and XML parsing pass.
- [ ] Odoo.sh test build loaded the registry and upgraded the affected modules.
- [ ] Odoo module tests tagged `at_install` passed in the Odoo.sh test build.
- [ ] No secrets, generated output, database dumps, or customer exports are included.
- [ ] Write-capable automation remains paused during deployment.
- [ ] A current labeled Odoo.sh database backup exists.
- [ ] Read-only smoke tests and rollback ownership are recorded.

## Data-change controls

- [ ] Dry-run evidence is attached.
- [ ] Record count and company scope are bounded.
- [ ] Idempotency key and business reason are recorded.
- [ ] Apply approval is explicit and expires after the run.
- [ ] Artifact schema, SHA-256, S3 URI, and archive verification are recorded.
