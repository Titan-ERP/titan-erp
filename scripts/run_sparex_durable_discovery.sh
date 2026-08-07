#!/usr/bin/env bash
set -euo pipefail

install_root="${CATALOG_AGENT_INSTALL_ROOT:-/opt/southern-parts/catalog-agent}"
runtime="${install_root}/current"
odoo_env="${ODOO_ENV_FILE:-/opt/southern-parts/Odoo/odoo_connection.env}"
artifact_root="${install_root}/artifacts/discovery"
lock_file="${SPAREX_DISCOVERY_LOCK_FILE:-/run/titan-sparex-catalog/durable-discovery.lock}"

fail_closed() {
  status=$?
  trap - ERR
  set +e
  echo "Durable Sparex discovery failed; disabling its timer for supervised review." >&2
  systemctl disable --now titan-sparex-durable-discovery.timer 2>/dev/null
  exit "${status}"
}
trap fail_closed ERR

exec 9>"${lock_file}"
if ! flock -n 9; then
  echo "A durable Sparex discovery worker already owns the host lock."
  exit 0
fi

test "$(df -Pk /opt | awk 'NR == 2 {print $4}')" -ge 2097152
test -n "${SPAREX_CATALOG_QUEUE_URL:-}"
test -n "${SOUTHERN_PRODUCT_ARTIFACT_BUCKET:-}"
test "${AWS_DEFAULT_REGION:-${AWS_REGION:-}}" = "us-east-1"
test -f "${odoo_env}"
test -d "${runtime}"

mkdir -p "${artifact_root}"
export ODOO_WRITE_ENABLED=true
export ODOO_API_MODE=json2
export PYTHONPATH="${runtime}"

"${install_root}/venv/bin/python" -m scripts.sparex_catalog_discovery \
  --odoo-env-file "${odoo_env}" \
  --dealer-env-file "${odoo_env}" \
  --artifact-root "${artifact_root}" \
  --s3-bucket "${SOUTHERN_PRODUCT_ARTIFACT_BUCKET}" \
  --run-key sparex-full-catalog-inventory-v3-cycle-26 \
  --max-pages-per-checkpoint 50 \
  --throttle-seconds 3.0 \
  --manifest-queue-url "${SPAREX_CATALOG_QUEUE_URL}" \
  --apply \
  --confirm sparex-discovery-queue \
  --reason "Continuous durable Sparex listing discovery after accepted canaries"
