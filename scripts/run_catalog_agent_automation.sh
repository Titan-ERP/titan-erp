#!/usr/bin/env bash
set -euo pipefail

install_root="${CATALOG_AGENT_INSTALL_ROOT:-/opt/southern-parts/catalog-agent}"
runtime_root="${install_root}/current"
artifact_root="${install_root}/artifacts"
odoo_env="${ODOO_ENV_FILE:-/opt/southern-parts/Odoo/odoo_connection.env}"
openai_parameter="${OPENAI_KEY_PARAMETER:-/southern-parts/sparex-odoo/OPENAI_API_KEY}"

mkdir -p "${artifact_root}"
exec 9>"${artifact_root}/catalog-agent.lock"
flock -n 9 || exit 0

OPENAI_API_KEY="$(aws ssm get-parameter \
  --name "${openai_parameter}" \
  --with-decryption \
  --query 'Parameter.Value' \
  --output text \
  --region us-east-1)"
if [[ -z "${OPENAI_API_KEY}" || "${OPENAI_API_KEY}" == "None" ]]; then
  echo "Catalog agent stopped: OpenAI key parameter is unavailable." >&2
  exit 1
fi

export OPENAI_API_KEY
export ODOO_WRITE_ENABLED=true
export PYTHONPATH="${runtime_root}"
export SOUTHERN_PRODUCT_ARTIFACT_BUCKET="southern-parts-catalog-artifacts-475369996980-us-east-1"

exec "${install_root}/venv/bin/python" -m scripts.sparex_catalog_agents.orchestrator \
  --odoo-env-file "${odoo_env}" \
  --artifact-root "${artifact_root}" \
  --limit 5 \
  --throttle-seconds 3 \
  --run-ai \
  --apply \
  --publish \
  --confirm catalog-agent-automation \
  --reason "Approved continuous catalog verification and website publication"
