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
- The discovery worker cannot create products, change prices, or publish
  products. The deterministic release worker may fill only a missing exact
  verified source URL or missing listing image immediately before release;
  those writes have their own plan, rollback artifact, and exact confirmation.
- Supplier URLs remain internal. User-facing status reports use hashes and
  counts rather than private URLs.

## Checkpoint contract

Each checkpoint:

1. Acquires a short Odoo lease so duplicate workers cannot overlap.
2. Resumes five explicit cursors while new or recovering, then up to ten when
   healthy, from a bounded deduplicated frontier bound to an immutable plan.
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
10. Prioritizes pagination/product-dense listing URLs before broad category
    expansion and adds only same-host listing links to a 10,000-URL frontier.
11. Advances each cursor only after the archived page is recorded successfully.
12. Marks every SKU seen by the current run as current evidence. When the run
    completes, records not seen by that run become stale, lose publication
    eligibility, and remain available for review rather than being deleted.

A transport or parser failure preserves the cursor. A true portal warning,
login failure, HTTP 429, or qualifying 5xx response starts a 60-minute cooldown.

## Odoo interface

System administrators can inspect:

- **Parts Intelligence → Sparex Discovery Runs** for cursor, lease, checkpoint,
  and aggregate match state.
- **Parts Intelligence → Sparex Discovery Queue** for exact SKU coverage,
  missing products, duplicates, source review, and publication candidates.

The Odoo menus include a progress dashboard and a separate missing-product
approval queue. Missing records show **Creation Review Required** and can be
approved or rejected for a future, separate creation workflow. Discovery alone
never creates an unenumerated Odoo product.

The dashboard also separates publication blockers for missing dealer cost,
sales price, exact product URL, product image, and source review. Each release
run refreshes up to 500 of the least-recently checked current records, so older
Odoo products are continuously re-evaluated instead of depending only on new
discoveries.

Products whose only leading blocker is dealer cost enter the dedicated
**Sparex Dealer Cost Recovery** queue. Records with an existing sales price,
exact product URL, and image receive the highest priority. Recovery claims use
row locks, stable worker ownership, exponential retry scheduling, and a
five-attempt manual-review threshold. This queue never writes supplier cost:
verified dealer-cost evidence must still be applied by the existing supervised
price workflow before the record can become publication-ready.

Publication selection now starts from current, refreshed discovery records
already marked publication-ready rather than repeatedly scanning the first
2,000 product templates. This keeps older corrected records moving while
preserving exact SKU, source, image, cost, and publication safeguards.

Missing URL/image repair never overwrites a valid existing value. Listing image
bytes are HTTPS-fetched by the worker without retries, limited to 10 MiB,
checksum verified, and covered by the same locked rollback workflow. Supplier
cost, sales price, standard cost, accounting data, and unrelated content remain
invariant.

## Commands

Read-only parser/authentication check:

```powershell
python -m scripts.sparex_catalog_discovery `
  --odoo-env-file odoo_connection.env `
  --dealer-env-file odoo_connection.env `
  --run-key sparex-full-catalog-inventory-v1
```

Supervised adaptive queue checkpoint (five pages while new/recovering, up to
ten when healthy):

```powershell
$env:ODOO_WRITE_ENABLED = "true"
python -m scripts.sparex_catalog_discovery `
  --odoo-env-file odoo_connection.env `
  --dealer-env-file odoo_connection.env `
  --run-key sparex-full-catalog-inventory-v1 `
  --max-pages-per-checkpoint 10 `
  --apply `
  --confirm sparex-discovery-queue `
  --reason "Approved throttled Sparex listing inventory and Odoo match classification"
```

Production uses `titan-sparex-discovery.service` and
`titan-sparex-discovery.timer`. Both the discovery and publication services use
the same non-blocking lock, so they cannot overlap. Install the unit files only
after the Odoo module upgrade and a bounded read-only portal check.

Every successful discovery service completion triggers
`titan-catalog-agent.service`. The independent publication timer remains
disabled so there is one non-overlapping discovery-to-release path. Parser version v3 collapses
duplicate anchors for the same exact product URL and keeps the image-backed
listing card, while genuine conflicting URLs, images, or explicit SKUs remain
in review. The v3 run key replays the catalog so items previously classified by
the older parser are reconciled instead of silently reused. Publication now
requires a current-run discovery record; stale evidence cannot qualify.
