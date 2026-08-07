#!/usr/bin/env bash
set -euo pipefail

install_root="${CATALOG_AGENT_INSTALL_ROOT:-/opt/southern-parts/catalog-agent}"
runtime="${install_root}/current"
odoo_env="${ODOO_ENV_FILE:-/opt/southern-parts/Odoo/odoo_connection.env}"
artifact_root="${install_root}/artifacts/discovery"
lock_file="${SPAREX_DISCOVERY_LOCK_FILE:-/run/titan-sparex-catalog/durable-discovery.lock}"

fail_closed() {
  status=$?
  line=$1
  command=$2
  trap - ERR
  set +e
  echo "Durable Sparex discovery failed at line ${line}: ${command}" >&2
  echo "Disabling its timer for supervised review." >&2
  systemctl disable --now titan-sparex-durable-discovery.timer 2>/dev/null
  exit "${status}"
}
trap 'fail_closed "${LINENO}" "${BASH_COMMAND}"' ERR

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

mkdir -p "${artifact_root}" "${install_root}/artifacts/cost" "${install_root}/artifacts/release"
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

"${install_root}/venv/bin/python" -m scripts.sparex_catalog_cost_worker \
  --odoo-env-file "${odoo_env}" \
  --dealer-env-file "${odoo_env}" \
  --artifact-root "${install_root}/artifacts/cost" \
  --s3-bucket "${SOUTHERN_PRODUCT_ARTIFACT_BUCKET}" \
  --limit 5 \
  --throttle-seconds 3.0 \
  --confirm sparex-durable-cost-recovery \
  --reason "Continuous exact dealer-cost recovery into durable staging"

"${install_root}/venv/bin/python" -m scripts.sparex_catalog_media_worker \
  --odoo-env-file "${odoo_env}" \
  --s3-bucket "${SOUTHERN_PRODUCT_ARTIFACT_BUCKET}" \
  --limit 25 \
  --throttle-seconds 3.0

"${install_root}/venv/bin/python" -m scripts.sparex_catalog_promotion_worker \
  --odoo-env-file "${odoo_env}" \
  --artifact-uri-prefix "s3://${SOUTHERN_PRODUCT_ARTIFACT_BUCKET}/sparex-product-catalog/promotion/production" \
  --limit 100

echo "Completed one protected Sparex internal-catalog cycle; website publication remains independent."
