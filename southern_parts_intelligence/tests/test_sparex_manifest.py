from unittest.mock import patch

from odoo.tests.common import TransactionCase

from ..models.sparex_manifest import canonical_sha256

ARTIFACT_SHA = "a" * 64


class SparexManifestTests(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Sparex Manifest Vendor", "supplier_rank": 1})
        cls.category = cls.env["product.category"].create({"name": "Sparex Manifest Pending"})
        cls.source = cls.env["southern.vendor.catalog.source"].search(
            [("company_id", "=", cls.env.company.id), ("code", "=", "sparex")], limit=1
        )
        if not cls.source:
            cls.source = cls.env["southern.vendor.catalog.source"].create(
                {
                    "name": "Sparex",
                    "code": "sparex",
                    "partner_id": cls.vendor.id,
                    "source_type": "web_listing",
                    "base_url": "https://us.sparex.com",
                    "internal_reference_prefix": "",
                    "default_category_id": cls.category.id,
                }
            )

    def _contract(self, records, run_id="run-1", sweep_id="sweep-1"):
        payload_sha = canonical_sha256(records)
        manifest = {
            "schema_version": "sparex-manifest-v1",
            "parser_version": "19.0.1.45.0",
            "run_id": run_id,
            "sweep_id": sweep_id,
            "page_range": "1-5",
            "record_count": len(records),
            "payload_sha256": payload_sha,
            "source_artifacts": [{"uri": "s3://catalog/pages/1-5.json", "sha256": ARTIFACT_SHA}],
        }
        return manifest, canonical_sha256(manifest)

    def _record(self, sku):
        return {
            "vendor_sku": sku,
            "title": f"Sparex {sku}",
            "source_url": f"https://us.sparex.com/product-{sku.casefold().replace('.', '-')}.html",
            "image_url": f"https://cdn.example.com/{sku.casefold().replace('.', '-')}.jpg",
        }

    def test_transient_record_failure_commits_retry_state(self):
        records = [self._record("S.100")]
        manifest, manifest_sha = self._contract(records)
        ItemClass = type(self.env["southern.vendor.catalog.item"])
        with patch.object(ItemClass, "upsert_catalog_items", autospec=True, side_effect=RuntimeError("database busy")):
            result = self.env["southern.sparex.catalog.ingestion"].ingest_manifest(
                manifest, records, manifest_sha
            )
        ingestion = self.env["southern.sparex.catalog.ingestion"].search(
            [("manifest_sha256", "=", manifest_sha)]
        )
        self.assertEqual(result["state"], "retry_required")
        self.assertEqual(result["transient_rejected"], 1)
        self.assertEqual(ingestion.state, "failed")
        self.assertFalse(ingestion.completed_at)

    def test_permanent_record_failure_does_not_retry_manifest(self):
        records = [{"vendor_sku": "", "source_url": "https://us.sparex.com/invalid.html"}]
        manifest, manifest_sha = self._contract(records, run_id="run-permanent")
        result = self.env["southern.sparex.catalog.ingestion"].ingest_manifest(
            manifest, records, manifest_sha
        )
        self.assertEqual(result["state"], "complete")
        self.assertEqual(result["permanent_rejected"], 1)
        self.assertEqual(result["transient_rejected"], 0)

    def test_absence_requires_two_sweeps_without_observation(self):
        Item = self.env["southern.vendor.catalog.item"]
        Item.with_context(sparex_sweep_key="sweep-1").upsert_catalog_items(
            "sparex", [self._record("S.201")], "s3://catalog/sweep-1.json", ARTIFACT_SHA
        )
        Item.with_context(sparex_sweep_key="older-sweep").upsert_catalog_items(
            "sparex", [self._record("S.202")], "s3://catalog/older.json", ARTIFACT_SHA
        )
        Item.upsert_catalog_items(
            "sparex", [self._record("S.203")], "s3://catalog/no-sweep.json", ARTIFACT_SHA
        )
        Sweep = self.env["southern.sparex.catalog.sweep"]
        for key in ("sweep-1", "sweep-2"):
            sweep_id = Sweep.record_checkpoint(
                {
                    "sweep_key": key,
                    "parser_version": "19.0.1.45.0",
                    "rules_version": "rules-v1",
                    "frontier_page_count": 1,
                    "processed_page_count": 1,
                    "evidence_uri": f"s3://catalog/{key}.json",
                    "evidence_sha256": ARTIFACT_SHA,
                }
            )
            Sweep.browse(sweep_id).action_complete()
        second = Sweep.search([("sweep_key", "=", "sweep-2")])
        self.assertEqual(second.consecutive_complete_count, 2)
        self.assertEqual(second.absence_candidate_count, 2)
