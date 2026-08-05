# Sparex Sourcing Control

## Operating decision

Sparex products remain unpublished unless the sourcing queue proves all of the
following:

- exact `S.%` product match;
- current positive Sparex supplier cost in company currency;
- exact HTTPS Sparex evidence URL and SHA-256 evidence hash;
- evidence retrieved within the last 30 days;
- approved retail price satisfying the configured margin;
- website category, image, and customer-facing description.

Supplier sourcing never writes `product.template.standard_price`. Approved cost
is applied only to `product.supplierinfo`. Inventory valuation and accounting
standard-cost changes remain a separate accounting workflow.

## Workflow

```text
Plan ten products
  -> source exact dealer evidence
  -> stage evidence in Odoo
  -> approve supplier cost
  -> apply product.supplierinfo
  -> approve retail and margin
  -> apply approved retail
  -> publish eligible products
```

The external worker does not select the newest local CSV or JSON file. Every
downstream stage requires both an explicit input path and its SHA-256 hash.
Use `--archive-s3 --s3-bucket <bucket>` to upload and verify immutable artifact
integrity metadata. S3 archival is explicit and never enabled by default.

## Commands

Create a ten-product plan after the module upgrade:

```powershell
$env:PYTHONPATH = "$PWD\scripts"
py -3 scripts\sparex_sourcing_pipeline.py `
  --env-file C:\secure\odoo_connection.env `
  plan --limit 10
```

Source the exact plan. Copy the artifact path and SHA-256 printed by the plan;
do not substitute a glob or `latest` file:

```powershell
py -3 scripts\sparex_sourcing_pipeline.py `
  --env-file C:\secure\odoo_connection.env `
  --dealer-env-file C:\secure\sparex.env `
  source --input C:\evidence\plan.json --input-sha256 <sha256>
```

Stage the evidence in Odoo after reviewing the evidence manifest:

```powershell
$env:ODOO_WRITE_ENABLED = "true"
py -3 scripts\sparex_sourcing_pipeline.py `
  --env-file C:\secure\odoo_connection.env `
  apply-evidence --input C:\evidence\evidence.json --input-sha256 <sha256> `
  --apply --confirm sparex-stage-evidence `
  --reason "Reviewed ten-product Sparex sourcing batch" --max-records 10
Remove-Item Env:\ODOO_WRITE_ENABLED
```

Cost and retail approvals are completed from **Inventory > Configuration >
Parts Intelligence > Sparex Sourcing Control**. Only rows in `publication_ready`
state can be published by the final command.

## Retry policy

- A failed SKU enters a seven-day cooldown.
- Three attempts move it to manual review.
- Ambiguous prices move directly to manual review.
- Generic dollar amounts are rejected; only product-structured prices are used.
- A manual-review or rejected row is excluded from subsequent plans.

## Website rollback

`odoo_sparex_publication_safeguard.py` creates a JSON snapshot before clearing
publication fields. Restore requires that exact snapshot plus the normal apply
gate:

```powershell
$env:ODOO_WRITE_ENABLED = "true"
py -3 scripts\odoo_sparex_publication_safeguard.py `
  --env-file C:\secure\odoo_connection.env `
  --restore-from C:\evidence\sparex_publication_snapshot.json `
  --apply --confirm sparex-publication-safeguard `
  --reason "Approved rollback" --max-records 4000
Remove-Item Env:\ODOO_WRITE_ENABLED
```
