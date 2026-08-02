# Sparex catalog discovery

The Sparex discovery queue inventories authenticated Sparex listing pages and
compares every exact SKU to Odoo. It complements the existing website-release
pipeline; it does not replace the five Odoo catalog agents.

## Ownership and boundaries

- The existing **Sparex Discovery Agent** owns exact listing-link and image
  interpretation.
- The existing **Odoo Match Agent** owns existing, missing, archived, and
  duplicate classification.
- Deterministic application code performs requests, hashing, archival,
  pagination, and exact SKU matching.
- The discovery worker cannot create products, change prices, copy images into
  products, or publish products.
- Supplier URLs remain internal. User-facing status reports use hashes and
  counts rather than private URLs.

## Checkpoint contract

Each checkpoint:

1. Acquires a short Odoo lease so duplicate workers cannot overlap.
2. Resumes one explicit cursor from a bounded, deduplicated listing/category frontier bound to an immutable plan.
3. Authenticates to Sparex and opens only the listing page. It never requests a
   discovered product-detail URL.
4. Waits at least three seconds between every portal request and performs no
   HTTP retries.
5. Extracts exact `S.%` product links and the image located on the same listing
   card.
6. Archives the page result to the established private S3 bucket with verified
   SHA-256 metadata.
7. Upserts `southern.sparex.discovery.item` and classifies the exact SKU as an
   active Odoo match, archived match, missing product, or duplicate SKU.
8. For matched products, records the four publication facts: positive existing
   Sparex supplier cost, positive existing sales price, exact Sparex URL, and
   image presence.
9. Separates products ready for source/image enrichment from products whose
   source URL and image are already stored in Odoo and ready for publication.
10. Adds same-host category/listing links, never product-detail links, to a 10,000-URL bounded frontier.
11. Advances the cursor only after the archived page is recorded successfully.

A transport or parser failure preserves the cursor. A true portal warning,
login failure, HTTP 429, or qualifying 5xx response starts a 60-minute cooldown.

## Odoo interface

System administrators can inspect:

- **Parts Intelligence → Sparex Discovery Runs** for cursor, lease, checkpoint,
  and aggregate match state.
- **Parts Intelligence → Sparex Discovery Queue** for exact SKU coverage,
  missing products, duplicates, source review, and publication candidates.

Missing records intentionally show **Product Creation Not Authorized**. A
future product-creation workflow requires separate rules, approval, and
rollback; discovery alone never creates an unenumerated Odoo product.

## Commands

Read-only parser/authentication check:

```powershell
python -m scripts.sparex_catalog_discovery `
  --odoo-env-file odoo_connection.env `
  --dealer-env-file odoo_connection.env `
  --run-key sparex-full-catalog-inventory-v1
```

Supervised one-page queue checkpoint:

```powershell
$env:ODOO_WRITE_ENABLED = "true"
python -m scripts.sparex_catalog_discovery `
  --odoo-env-file odoo_connection.env `
  --dealer-env-file odoo_connection.env `
  --run-key sparex-full-catalog-inventory-v1 `
  --apply `
  --confirm sparex-discovery-queue `
  --reason "Approved throttled Sparex listing inventory and Odoo match classification"
```

Production uses `titan-sparex-discovery.service` and
`titan-sparex-discovery.timer`. Both the discovery and publication services use
the same non-blocking lock, so they cannot overlap. Install the unit files only
after the Odoo module upgrade and a bounded read-only portal check.

Every successful discovery service completion triggers
`titan-catalog-agent.service`. This provides a deterministic discovery-to-release
handoff in addition to the publication timer. Parser version v3 collapses
duplicate anchors for the same exact product URL and keeps the image-backed
listing card, while genuine conflicting URLs, images, or explicit SKUs remain
in review. The v3 run key replays the catalog so items previously classified by
the older parser are reconciled instead of silently reused.
