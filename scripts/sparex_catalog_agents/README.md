# Odoo Sparex Catalog Agents

This bounded OpenAI Agents SDK worker processes tasks owned by the
`southern_parts_intelligence` Odoo module.

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

The production runner retrieves `OPENAI_API_KEY` from AWS Systems Manager
Parameter Store at runtime. The key is never stored in Odoo, the repository,
the service definition, or an artifact.

Install the optional local dependency:

```powershell
python -m pip install -e ".[agents,dev]"
```

Inspect the next bounded queue without calling OpenAI or writing Odoo:

```powershell
python -m scripts.sparex_catalog_agents.worker --agent product_verification --limit 5
```

An API call requires explicit `--run-ai`. Recording results also requires the
normal `ODOO_WRITE_ENABLED=true`, `--apply`, `--confirm catalog-agent-results`,
and a business reason. Product writes remain outside this worker.

Run the complete chain in read-only preview mode:

```powershell
python -m scripts.sparex_catalog_agents.orchestrator --odoo-env-file odoo_connection.env --limit 5
```

The supervised apply path requires all controls and an S3 artifact bucket:

```powershell
$env:ODOO_WRITE_ENABLED = "true"
$env:SOUTHERN_PRODUCT_ARTIFACT_BUCKET = "southern-parts-catalog-artifacts-475369996980-us-east-1"
python -m scripts.sparex_catalog_agents.orchestrator `
  --odoo-env-file odoo_connection.env --run-ai --apply --publish `
  --confirm catalog-agent-automation `
  --reason "Approved catalog verification and website publication"
```

Production scheduling uses `cloud/aws/titan-catalog-agent.service` and
`cloud/aws/titan-catalog-agent.timer`. The oneshot service plus `flock` prevents
overlap; the timer waits two minutes after a completed run before scheduling
the next batch of at most five products.

Whole-catalog coverage is handled separately by
`scripts/sparex_catalog_discovery.py`. It reuses the Discovery and Match agent
ownership, walks only authenticated listing pages, stores a resumable Odoo
cursor and match queue, and never creates products. See
`docs/SPAREX_CATALOG_DISCOVERY.md`.
