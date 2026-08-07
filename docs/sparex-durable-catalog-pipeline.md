# Sparex Durable Catalog Pipeline

This implementation separates vendor access, immutable evidence, Odoo catalog readiness, and website publication.

## Runtime flow

1. `scripts.sparex_catalog_discovery` uses one authenticated, sequential Sparex listing-page session with a minimum three-second request interval.
2. Every raw and parsed page is archived in S3. Checkpoints publish canonical manifests of at most 500 records to `titan-sparex-catalog.fifo` with message group `vendor:sparex:catalog`.
3. `scripts.sparex_catalog_queue_worker` verifies the manifest, payload, and every referenced source artifact before calling Odoo. It extends SQS visibility every 60 seconds and deletes the message only after Odoo returns a committed, completed ingestion result.
4. Odoo serializes staging, promotion, media, and reconciliation with `pg_advisory_xact_lock(0x535041524558::bigint)`. Manifest processing uses 50-record savepoints and bounded bisection for rejected records.
5. `scripts.sparex_catalog_media_worker` validates image bytes and dimensions, archives the original by content hash, and sends no image data through SQS.
6. `scripts.sparex_catalog_promotion_worker` creates or refreshes only blocker-free operational products. Website publication remains a separate state and workflow.

## Safe rollout

The CloudFormation template creates the FIFO queue, five-attempt redrive policy, 14-day DLQ, alarms, and a managed worker policy. It does not start a crawler or enable a systemd timer.

1. Deploy and upgrade `southern_parts_intelligence`.
2. Run `scripts/sparex_catalog_conflict_preflight.py` and archive the report. Do not create the remaining production uniqueness indexes while the report is blocking.
3. Deploy `cloud/aws/sparex-catalog-pipeline.yaml` and attach its managed policy only to the catalog worker role.
4. Install the systemd units with `cloud/aws/install-sparex-catalog-pipeline.sh`; both timers remain disabled.
5. Configure `SPAREX_CATALOG_QUEUE_URL` in `/opt/southern-parts/catalog-agent/catalog-pipeline.env`.
6. Run two supervised five-page checkpoints. Stop immediately on any portal warning signal and retain the existing one-hour cooldown.
7. After two healthy runs at each level, advance to 20-page and then 50-page logical jobs. Recovery evidence is still committed every five pages.
8. Enable `titan-sparex-catalog-ingestion.timer` only after queue and Odoo ingestion telemetry are healthy.
9. Enable `titan-sparex-durable-discovery.timer` only after two healthy 50-page canaries and explicit operator approval. Its first run waits 15 minutes; subsequent runs start at least 20 minutes after the prior service becomes inactive. Each run uses a host lock and disables its timer on any worker failure. Portal cooldown remains enforced by the Odoo discovery run.

## State and ownership

Catalog state is independent from website state. A product may be operational internally while remaining `not_ready` for the website. Staff can protect manual name, image, category, description, or sales-price changes with explicit product override flags. The pipeline continues to own exact vendor identity, URL, cost, supplier information, availability, evidence, and pricing basis.

No crawler, queue consumer, media worker, promotion worker, or archival action is automatically enabled by installation. The durable discovery and ingestion timers require separate supervised activation.
