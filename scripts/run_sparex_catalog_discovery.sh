#!/usr/bin/env bash
set -euo pipefail

install_root="${CATALOG_AGENT_INSTALL_ROOT:-/opt/southern-parts/catalog-agent}"
runtime_root="${install_root}/current"
artifact_root="${install_root}/artifacts/discovery"
odoo_env="${ODOO_ENV_FILE:-/opt/southern-parts/Odoo/odoo_connection.env}"
run_key="${SPAREX_DISCOVERY_RUN_KEY:-sparex-full-catalog-inventory-v1}"

mkdir -p "${artifact_root}"
exec 9>"${install_root}/artifacts/catalog-agent.lock"
flock -n 9 || exit 0

export ODOO_WRITE_ENABLED=true
export ODOO_API_MODE=json2
export PYTHONPATH="${runtime_root}"
export SOUTHERN_PRODUCT_ARTIFACT_BUCKET="southern-parts-catalog-artifacts-475369996980-us-east-1"

exec "${install_root}/venv/bin/python" -m scripts.sparex_catalog_discovery \
  --odoo-env-file "${odoo_env}" \
  --dealer-env-file "${odoo_env}" \
  --artifact-root "${artifact_root}" \
  --run-key "${run_key}" \
  --throttle-seconds 3 \
  --apply \
  --confirm sparex-discovery-queue \
  --reason "Approved throttled Sparex listing inventory and Odoo match classification"
