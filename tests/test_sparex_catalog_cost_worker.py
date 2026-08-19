import unittest
from unittest.mock import patch

from scripts import sparex_catalog_cost_worker as worker


class SparexCatalogCostWorkerTests(unittest.TestCase):
    def test_confirmation_is_stable(self):
        self.assertEqual(worker.CONFIRMATION, "sparex-durable-cost-recovery")
        self.assertEqual(worker.MAX_AUTOMATIC_COST_RECOVERY_BATCH, 15)

    def test_portal_cooldown_returns_distinct_recoverable_exit(self):
        args = [
            "worker",
            "--odoo-env-file", "odoo.env",
            "--dealer-env-file", "dealer.env",
            "--artifact-root", "artifacts",
            "--s3-bucket", "bucket",
            "--confirm", worker.CONFIRMATION,
            "--reason", "test",
        ]
        with patch.dict("os.environ", {"ODOO_WRITE_ENABLED": "true"}), patch(
            "sys.argv", args
        ), patch.object(worker, "ArtifactStore"), patch.object(
            worker.OdooConfig, "from_env"
        ), patch.object(worker, "OdooClient") as client, patch.object(
            worker, "recover_dealer_costs", side_effect=worker.PortalCooldownError("warning")
        ):
            client.return_value.connect.return_value = object()
            self.assertEqual(worker.main(), worker.PORTAL_COOLDOWN_EXIT_CODE)

    def test_slow_success_requests_shared_portal_cooldown(self):
        args = [
            "worker",
            "--odoo-env-file", "odoo.env",
            "--dealer-env-file", "dealer.env",
            "--artifact-root", "artifacts",
            "--s3-bucket", "bucket",
            "--limit", "20",
            "--confirm", worker.CONFIRMATION,
            "--reason", "test",
        ]
        with patch.dict("os.environ", {"ODOO_WRITE_ENABLED": "true"}), patch(
            "sys.argv", args
        ), patch.object(worker, "ArtifactStore"), patch.object(
            worker.OdooConfig, "from_env"
        ), patch.object(worker, "OdooClient") as client, patch.object(
            worker,
            "recover_dealer_costs",
            return_value={"state": "succeeded", "slow_pages": 1, "write_blocked": False},
        ) as recover:
            client.return_value.connect.return_value = object()
            self.assertEqual(worker.main(), worker.PORTAL_COOLDOWN_EXIT_CODE)
            self.assertEqual(recover.call_args.kwargs["limit"], 15)

    def test_persisted_portal_cooldown_returns_distinct_recoverable_exit(self):
        args = [
            "worker",
            "--odoo-env-file", "odoo.env",
            "--dealer-env-file", "dealer.env",
            "--artifact-root", "artifacts",
            "--s3-bucket", "bucket",
            "--confirm", worker.CONFIRMATION,
            "--reason", "test",
        ]
        with patch.dict("os.environ", {"ODOO_WRITE_ENABLED": "true"}), patch(
            "sys.argv", args
        ), patch.object(worker, "ArtifactStore"), patch.object(
            worker.OdooConfig, "from_env"
        ), patch.object(worker, "OdooClient") as client, patch.object(
            worker,
            "recover_dealer_costs",
            return_value={"state": "portal_cooldown", "write_blocked": True},
        ):
            client.return_value.connect.return_value = object()
            self.assertEqual(worker.main(), worker.PORTAL_COOLDOWN_EXIT_CODE)


if __name__ == "__main__":
    unittest.main()
