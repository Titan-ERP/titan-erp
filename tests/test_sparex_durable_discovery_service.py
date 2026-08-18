import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SparexDurableDiscoveryServiceTests(unittest.TestCase):
    def test_launcher_is_bounded_locked_and_fail_closed(self):
        launcher = (ROOT / "scripts" / "run_sparex_durable_discovery.sh").read_text(encoding="utf-8")
        self.assertIn("flock -n 9", launcher)
        self.assertIn("/run/titan-sparex-catalog/durable-discovery.lock", launcher)
        self.assertIn('trap \'fail_closed "${LINENO}" "${BASH_COMMAND}"\' ERR', launcher)
        self.assertIn("2097152", launcher)
        self.assertIn("--max-pages-per-checkpoint 50", launcher)
        self.assertIn("--throttle-seconds 3.0", launcher)
        self.assertIn("--manifest-queue-url", launcher)
        self.assertIn("systemctl disable --now titan-sparex-durable-discovery.timer", launcher)
        self.assertIn("SPAREX_PORTAL_COOLDOWN_SECONDS:-3600", launcher)
        self.assertIn('[[ "${portal_cooldown_seconds}" -lt 3600 ]]', launcher)
        self.assertIn('if [[ "${status}" -eq 75 ]]', launcher)
        self.assertIn('portal_cooldown=1', launcher)
        self.assertIn('skipping portal access this cycle', launcher)
        self.assertIn("-m scripts.sparex_catalog_cost_worker", launcher)
        self.assertIn('SPAREX_COST_RECOVERY_LIMIT:-10', launcher)
        self.assertIn("-m scripts.sparex_catalog_media_worker", launcher)
        self.assertIn("-m scripts.sparex_catalog_promotion_worker", launcher)
        self.assertIn("run_internal_step media", launcher)
        self.assertIn("run_internal_step promotion", launcher)
        self.assertIn("SPAREX_INTERNAL_RETRY_LIMIT:-5", launcher)
        self.assertIn("Transient Odoo failure in", launcher)
        self.assertIn("Odoo JSON-2 request failed with HTTP (404|429|500|502|503|504)", launcher)
        self.assertIn('[[ "${retry_count}" -lt "${internal_retry_limit}" ]]', launcher)
        self.assertNotIn("-m scripts.sparex_catalog_agents.orchestrator", launcher)
        self.assertNotIn("--publish", launcher)
        self.assertNotIn("phase_file", launcher)
        self.assertIn("--throttle-seconds 3.0", launcher)
        self.assertNotIn("--create-missing-products", launcher)

    def test_timer_preserves_healthy_portal_spacing(self):
        timer = (ROOT / "cloud" / "aws" / "titan-sparex-durable-discovery.timer").read_text(
            encoding="utf-8"
        )
        service = (ROOT / "cloud" / "aws" / "titan-sparex-durable-discovery.service").read_text(
            encoding="utf-8"
        )
        installer = (ROOT / "cloud" / "aws" / "install-sparex-catalog-pipeline.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("OnActiveSec=15min", timer)
        self.assertIn("OnUnitInactiveSec=5min", timer)
        self.assertIn("Unit=titan-sparex-durable-discovery.service", timer)
        self.assertIn("run_sparex_durable_discovery.sh", service)
        self.assertIn("RuntimeDirectory=titan-sparex-catalog", service)
        self.assertIn("Environment=SOUTHERN_PRODUCT_ARTIFACT_BUCKET=", service)
        self.assertIn("ProtectSystem=strict", service)
        self.assertIn("disable --now titan-sparex-durable-discovery.timer", installer)

    def test_website_publication_launcher_does_not_require_executable_file_mode(self):
        service = (ROOT / "cloud" / "aws" / "titan-sparex-website-publication.service").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "ExecStart=/bin/bash "
            "/opt/southern-parts/catalog-agent/current/scripts/run_sparex_website_publication.sh",
            service,
        )


if __name__ == "__main__":
    unittest.main()
