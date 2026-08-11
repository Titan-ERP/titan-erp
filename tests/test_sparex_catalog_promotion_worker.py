import io
import json
import sys
import unittest
from unittest.mock import Mock, call, patch

from scripts import sparex_catalog_promotion_worker as worker


class SparexCatalogPromotionWorkerTests(unittest.TestCase):
    def test_exact_operational_batch_repairs_only_linked_discovery_items(self):
        client = Mock()

        def odoo_call(model, method, **kwargs):
            if model == "southern.vendor.catalog.item" and method == "search_read":
                if kwargs.get("fields") == ["id", "match_state"]:
                    self.assertIn(("id", "in", [41]), kwargs["domain"])
                    self.assertNotIn(("catalog_state", "=", "ready_for_promotion"), kwargs["domain"])
                    return [{"id": 41, "match_state": "matched"}]
                return [{"id": 41, "normalized_sku": "S.40474", "product_id": [501, "S.40474"]}]
            if model == "southern.vendor.catalog.item" and method == "apply_operational_batch":
                return [{"item_id": 41, "product_id": 501, "product_fields_written": []}]
            if model == "southern.sparex.discovery.item" and method == "search_read":
                self.assertEqual(kwargs["domain"][-2:], [
                    ("matched_product_id", "in", [501]),
                    ("normalized_sku", "in", ["S.40474"]),
                ])
                return [{"id": 601}]
            if model == "southern.sparex.discovery.item" and method == "prepare_description_repair_plan":
                self.assertEqual(kwargs["item_ids"], [601])
                return [{"item_id": 601, "product_id": 501, "snapshot_sha256": "a" * 64}]
            if model == "southern.sparex.discovery.item" and method == "apply_description_repair_plan":
                return [{"item_id": 601, "product_id": 501}]
            self.fail(f"Unexpected Odoo call: {model}.{method}")

        client.call.side_effect = odoo_call
        connector = Mock()
        connector.connect.return_value = client
        archive_result = ("s3://catalog/description-repair/plan.json", "b" * 64)
        argv = [
            "sparex_catalog_promotion_worker.py",
            "--odoo-env-file",
            "odoo.env",
            "--artifact-uri-prefix",
            "s3://catalog/promotion/production",
            "--item-id",
            "41",
        ]
        output = io.StringIO()
        with (
            patch.object(worker.OdooConfig, "from_env", return_value=object()),
            patch.object(worker, "OdooClient", return_value=connector),
            patch.object(worker, "_archive_plan", return_value=archive_result) as archive,
            patch.object(sys, "argv", argv),
            patch("sys.stdout", output),
        ):
            self.assertEqual(worker.main(), 0)

        result = json.loads(output.getvalue())
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["description_repaired"][0]["item_id"], 601)
        self.assertEqual(result["description_artifacts"][0]["artifact_sha256"], "b" * 64)
        archive.assert_has_calls(
            [
                call(
                    "s3://catalog/promotion/production",
                    "description-repair",
                    [{"item_id": 601, "product_id": 501, "snapshot_sha256": "a" * 64}],
                )
            ]
        )


if __name__ == "__main__":
    unittest.main()
