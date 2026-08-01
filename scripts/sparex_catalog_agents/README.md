# Odoo Sparex Catalog Agents

This bounded OpenAI Agents SDK worker processes tasks owned by the
`southern_parts_intelligence` Odoo module.

The five profiles are:

1. Catalog Coordinator
2. Sparex Discovery Agent
3. Odoo Match Agent
4. Product Verification Agent
5. Website Release Agent

Odoo owns profiles, task state, exact SKU matching, readiness facts, and
results. The worker reads `OPENAI_API_KEY` from `.env.local`; the key is never
stored in Odoo. The worker cannot create products, change prices, or publish
products.

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
