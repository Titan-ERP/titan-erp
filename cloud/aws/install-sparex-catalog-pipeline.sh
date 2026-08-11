#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this installer as root." >&2
  exit 1
fi

runtime_root="${CATALOG_AGENT_RUNTIME_ROOT:-/opt/southern-parts/catalog-agent/current}"
unit_root="${SYSTEMD_UNIT_ROOT:-/etc/systemd/system}"

for unit in \
  titan-sparex-catalog-ingestion.service \
  titan-sparex-catalog-ingestion.timer \
  titan-sparex-durable-discovery.service \
  titan-sparex-durable-discovery.timer \
  titan-sparex-website-publication.service \
  titan-sparex-website-publication.timer; do
  test -f "${runtime_root}/cloud/aws/${unit}"
  install -m 0644 "${runtime_root}/cloud/aws/${unit}" "${unit_root}/${unit}"
done

systemctl daemon-reload
systemctl disable --now titan-sparex-catalog-ingestion.timer 2>/dev/null || true
systemctl disable --now titan-sparex-durable-discovery.timer 2>/dev/null || true
systemctl disable --now titan-sparex-website-publication.timer 2>/dev/null || true
echo "Installed the Sparex catalog ingestion, durable discovery, and website publication units in a disabled state."
echo "Enable only after the Odoo module upgrade, conflict preflight, and supervised canaries pass."
