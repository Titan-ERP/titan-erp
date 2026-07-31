# Odoo Operations Control

The automation uses live Odoo through XML-RPC for authoritative reads and
controlled writes. Generated import files are evidence, not the source of truth.

## Safety contract

- Every migrated mutation workflow defaults to dry-run.
- A write requires `--apply`, `ODOO_WRITE_ENABLED=true`, a workflow-specific
  `--confirm` value, an approval `--reason`, and a supervised record limit.
- The standard minimum free-space threshold is 2 GB.
- Write authorization is recorded in `outputs/write_audit/odoo_writes.jsonl`.
- Generated CSV and JSON artifacts include schema version 1.0, SHA-256 hashes,
  record counts, and timestamps in a local manifest.
- S3 archive manifests include schema version and SHA-256 for every object.

## Authoritative controls

- `odoo_product_master_quality_queue.py` builds one queue for pricing,
  evidence, taxonomy, duplicates, images, descriptions, and publication.
- `odoo_crm_pipeline_report.py` separates mass-import reference records from
  commercially worked opportunities and scans the full dataset.
- `odoo_contact_preimport_match.py` matches incoming contacts before creation;
  ambiguous top matches are always routed to review.
- `odoo_operations_control_dashboard.py` creates the daily cross-functional
  accounting, sales, service, CRM, product, and automation snapshot.

## Deployment

GitHub pull requests run Python compilation, standalone regression tests, and
XML parsing. Production Odoo credentials stay in untracked environment files.
No deployment job is permitted to contain or print live credentials.
