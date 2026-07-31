import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts.odoo_runtime.artifacts import MIN_FREE_BYTES, ArtifactStore, sha256_file
from scripts.odoo_runtime.client import OdooClient, OdooConfig
from scripts.odoo_runtime.matching import (
    ContactCandidate,
    ContactIdentity,
    choose_contact_match,
)
from scripts.odoo_runtime.safety import ApplyGate, WriteBlocked


class _Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.value).encode()


class _S3:
    def __init__(self):
        self.objects = {}

    def upload_file(self, filename, bucket, key, ExtraArgs):
        self.objects[(bucket, key)] = {
            "body": Path(filename).read_bytes(),
            "Metadata": ExtraArgs["Metadata"],
        }

    def head_object(self, Bucket, Key):
        return self.objects[(Bucket, Key)]


class OdooRuntimeTests(unittest.TestCase):
    def test_json2_request_uses_named_arguments_and_database_header(self):
        config = OdooConfig(
            url="https://example.odoo.com",
            database="production",
            api_key="secret-key",
            attempts=1,
        )
        with mock.patch(
            "urllib.request.urlopen", return_value=_Response([{"id": 7}])
        ) as opened:
            value = OdooClient(config).call(
                "res.partner",
                "search_read",
                domain=[["email", "=", "test@example.com"]],
                fields=["name"],
                limit=1,
            )
        self.assertEqual(value, [{"id": 7}])
        request = opened.call_args.args[0]
        self.assertEqual(
            request.full_url,
            "https://example.odoo.com/json/2/res.partner/search_read",
        )
        self.assertEqual(request.headers["X-odoo-database"], "production")
        self.assertEqual(
            json.loads(request.data),
            {
                "domain": [["email", "=", "test@example.com"]],
                "fields": ["name"],
                "limit": 1,
            },
        )

    def test_apply_gate_requires_explicit_supervision_and_bounds(self):
        gate = ApplyGate("catalog_sync", True, "catalog_sync", "approved", 2)
        with mock.patch.dict(os.environ, {"ODOO_WRITE_ENABLED": "true"}):
            gate.authorize(2)
            with self.assertRaises(WriteBlocked):
                gate.authorize(3)
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(WriteBlocked):
                gate.authorize(1)

    def test_contact_match_requires_review_when_top_score_is_ambiguous(self):
        decision = choose_contact_match(
            ContactIdentity("Titan Parts", "ops@example.com", "601-555-0100"),
            [
                ContactCandidate(1, "Titan Parts", "ops@example.com", "6015550100"),
                ContactCandidate(2, "Titan Parts", "ops@example.com", "6015550100"),
            ],
        )
        self.assertEqual(decision.status, "review")

    def test_artifacts_are_versioned_hashed_archived_and_pruned(self):
        self.assertEqual(MIN_FREE_BYTES, 2 * 1024**3)
        with tempfile.TemporaryDirectory() as temp:
            store = ArtifactStore(Path(temp), minimum_free_bytes=0)
            record = store.write_json("batch.json", {"ok": True})
            self.assertEqual(record["sha256"], sha256_file(Path(record["path"])))
            archived = store.archive_s3(
                record,
                bucket="test-artifacts",
                prefix="catalog",
                s3_client=_S3(),
            )
            self.assertTrue(archived["archive_verified"])
            self.assertTrue(archived["artifact_uri"].startswith("s3://"))

            old = datetime.now(timezone.utc) - timedelta(days=100)
            manifest = Path(temp) / "manifest.jsonl"
            row = json.loads(manifest.read_text(encoding="utf-8").strip())
            row["created_at_utc"] = old.isoformat()
            manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
            result = store.prune(retention_days=90)
            self.assertEqual(result["deleted"], 1)
            self.assertFalse(Path(record["path"]).exists())


if __name__ == "__main__":
    unittest.main()
