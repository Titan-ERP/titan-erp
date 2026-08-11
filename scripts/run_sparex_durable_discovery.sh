#!/usr/bin/env bash
set -euo pipefail

install_root="${CATALOG_AGENT_INSTALL_ROOT:-/opt/southern-parts/catalog-agent}"
runtime="${install_root}/current"
odoo_env="${ODOO_ENV_FILE:-/opt/southern-parts/Odoo/odoo_connection.env}"
artifact_root="${install_root}/artifacts/discovery"
lock_file="${SPAREX_DISCOVERY_LOCK_FILE:-/run/titan-sparex-catalog/durable-discovery.lock}"
portal_cooldown_file="${SPAREX_PORTAL_COOLDOWN_FILE:-${install_root}/artifacts/portal-cooldown-until}"
portal_cooldown_seconds="${SPAREX_PORTAL_COOLDOWN_SECONDS:-3600}"
portal_cooldown=0
if ! [[ "${portal_cooldown_seconds}" =~ ^[0-9]+$ ]] || [[ "${portal_cooldown_seconds}" -lt 3600 ]]; then
  portal_cooldown_seconds=3600
fi

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

run_portal_step() {
  local status
  if "$@"; then
    return 0
  else
    status=$?
  fi
  if [[ "${status}" -eq 75 ]]; then
    date -u -d "+${portal_cooldown_seconds} seconds" +%s >"${portal_cooldown_file}"
    portal_cooldown=1
    echo "Sparex portal warning recorded; portal access is paused for at least ${portal_cooldown_seconds} seconds." >&2
    return 0
  fi
  return "${status}"
}

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

if [[ -f "${portal_cooldown_file}" ]]; then
  cooldown_until="$(cat "${portal_cooldown_file}")"
  if [[ "${cooldown_until}" =~ ^[0-9]+$ ]] && [[ "$(date -u +%s)" -lt "${cooldown_until}" ]]; then
    portal_cooldown=1
    echo "Sparex portal cooldown is active until epoch ${cooldown_until}; skipping portal access this cycle." >&2
  else
    rm -f "${portal_cooldown_file}"
  fi
fi

if [[ "${portal_cooldown}" -eq 0 ]]; then
  run_portal_step "${install_root}/venv/bin/python" -m scripts.sparex_catalog_discovery \
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
fi

if [[ "${portal_cooldown}" -eq 0 ]]; then
  run_portal_step "${install_root}/venv/bin/python" -m scripts.sparex_catalog_cost_worker \
    --odoo-env-file "${odoo_env}" \
    --dealer-env-file "${odoo_env}" \
    --artifact-root "${install_root}/artifacts/cost" \
    --s3-bucket "${SOUTHERN_PRODUCT_ARTIFACT_BUCKET}" \
    --limit "${SPAREX_COST_RECOVERY_LIMIT:-10}" \
    --throttle-seconds 3.0 \
    --confirm sparex-durable-cost-recovery \
    --reason "Continuous exact dealer-cost recovery into durable staging"
fi

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
