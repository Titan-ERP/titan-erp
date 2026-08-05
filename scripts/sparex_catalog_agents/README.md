# Odoo Sparex Catalog Agents

This bounded catalog worker processes tasks owned by the
`southern_parts_intelligence` Odoo module. The scheduled production path is
deterministic and makes zero OpenAI API calls for normal product processing.

The five profiles are:

1. Catalog Coordinator
2. Sparex Discovery Agent
3. Odoo Match Agent
4. Product Verification Agent
5. Website Release Agent

Each profile has one least-privilege function tool:

| Agent | Function tool |
| --- | --- |
| Catalog Coordinator | `route_catalog_task` |
| Sparex Discovery Agent | `verify_sparex_listing` |
| Odoo Match Agent | `inspect_odoo_match` |
| Product Verification Agent | `evaluate_product_readiness` |
| Website Release Agent | `evaluate_release_gate` |

The tools read only the current Odoo-owned task snapshot. Hosted Web Search,
File Search, MCP, shell, computer-use, and publication-write tools are not
attached to these agents.

Odoo owns profiles, task state, exact SKU matching, readiness facts, handoffs,
publication snapshots, and results. The agents cannot create products, change
prices, or publish products. After every agent has verified the same exact
record, a separate deterministic release transaction can change only the
writable publication flags. It records the prior flags and price/cost/image/URL
hashes, verifies the public HTTP page, and performs a scoped rollback if public
verification fails.

OpenAI review is optional and reserved for explicitly marked ambiguous
exceptions. It is disabled in the scheduled production launcher. A supervised
AI review uses `gpt-5.6-luna`, a hard per-run invocation cap (maximum five), and
stops all further model calls after the first provider failure. The key is never
stored in Odoo, the repository, the service definition, or an artifact.

Install the optional local dependency:

```powershell
python -m pip install -e ".[agents,dev]"
```

Inspect the next bounded queue without calling OpenAI or writing Odoo:

```powershell
python -m scripts.sparex_catalog_agents.worker --agent product_verification --limit 50
```

An API call requires an explicit ambiguity plus `--run-ai`. Recording either
deterministic or optional AI results requires the normal
`ODOO_WRITE_ENABLED=true`, `--apply`, `--confirm catalog-agent-results`, and a
business reason. Product writes remain outside this worker.

Run the complete chain in read-only preview mode:

```powershell
python -m scripts.sparex_catalog_agents.orchestrator --odoo-env-file odoo_connection.env --limit 50
```

The supervised apply path requires all controls and an S3 artifact bucket:

```powershell
$env:ODOO_WRITE_ENABLED = "true"
$env:SOUTHERN_PRODUCT_ARTIFACT_BUCKET = "southern-parts-catalog-artifacts-475369996980-us-east-1"
python -m scripts.sparex_catalog_agents.orchestrator `
  --odoo-env-file odoo_connection.env --apply --publish `
  --confirm catalog-agent-automation `
  --reason "Approved catalog verification and website publication"
```

Production scheduling is owned by Odoo. `cloud/aws/titan-sparex-discovery.timer`
polls for one Odoo-owned dispatch and exits without portal access when the queue
is empty. Odoo queues release work only after approval; the independent catalog
timer stays disabled to prevent competing schedules. The oneshot service plus
`flock` prevents overlap. Both discovery and an approved deterministic release
process at most five items sequentially, with at least three seconds between
portal requests and no HTTP retry loop.

Whole-catalog coverage is handled separately by
`scripts/sparex_catalog_discovery.py`. It reuses the Discovery and Match agent
ownership, walks only authenticated listing pages, stores a resumable Odoo
cursor and match queue, and can pass exact missing listing records to Odoo's
separately approved unpublished-draft creation contract. See
`docs/SPAREX_CATALOG_DISCOVERY.md`.
