#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

runtime_root="${CATALOG_AGENT_RUNTIME_ROOT:-/opt/southern-parts/catalog-agent/current}"
unit_root="${SYSTEMD_UNIT_ROOT:-/etc/systemd/system}"

test -f "${runtime_root}/scripts/run_sparex_catalog_discovery.sh"
test -f "${runtime_root}/cloud/aws/titan-sparex-discovery.service"
test -f "${runtime_root}/cloud/aws/titan-sparex-discovery.timer"

install -m 0644 \
  "${runtime_root}/cloud/aws/titan-sparex-discovery.service" \
  "${unit_root}/titan-sparex-discovery.service"
install -m 0644 \
  "${runtime_root}/cloud/aws/titan-sparex-discovery.timer" \
  "${unit_root}/titan-sparex-discovery.timer"

systemctl disable --now titan-catalog-agent.timer 2>/dev/null || true
systemctl daemon-reload
systemctl enable --now titan-sparex-discovery.timer
systemctl --no-pager status titan-sparex-discovery.timer
