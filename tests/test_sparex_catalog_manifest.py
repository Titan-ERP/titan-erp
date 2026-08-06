import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.sparex_catalog_manifest import (  # noqa: E402
    MESSAGE_GROUP_ID,
    build_manifest,
    canonical_bytes,
    parse_s3_uri,
)

ARTIFACT = {"uri": "s3://catalog/pages/page-1.html", "sha256": "a" * 64}


class SparexCatalogManifestTests(unittest.TestCase):
    def test_manifest_is_canonical_and_uses_payload_hash(self):
        payload = [{"vendor_sku": "S.1", "vendor_cost": "10.25"}]
        manifest, manifest_sha, manifest_bytes = build_manifest(
            payload,
            parser_version="19.0.1.45.0",
            run_id="run-1",
            sweep_id="sweep-1",
            page_range="1-5",
            source_artifacts=[ARTIFACT],
        )
        self.assertEqual(manifest["payload_sha256"], hashlib.sha256(canonical_bytes(payload)).hexdigest())
        self.assertEqual(manifest_sha, hashlib.sha256(manifest_bytes).hexdigest())
        self.assertEqual(manifest_bytes, canonical_bytes(json.loads(manifest_bytes)))
        self.assertEqual(MESSAGE_GROUP_ID, "vendor:sparex:catalog")

    def test_manifest_rejects_binary_float_prices(self):
        with self.assertRaisesRegex(TypeError, "binary floating-point"):
            build_manifest(
                [{"vendor_sku": "S.1", "vendor_cost": 10.25}],
                parser_version="v1",
                run_id="run",
                sweep_id="sweep",
                page_range="1",
                source_artifacts=[ARTIFACT],
            )

    def test_manifest_enforces_record_limit_and_s3_uri(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 500"):
            build_manifest(
                [],
                parser_version="v1",
                run_id="run",
                sweep_id="sweep",
                page_range="1",
                source_artifacts=[ARTIFACT],
            )
        self.assertEqual(parse_s3_uri("s3://bucket/prefix/key.json"), ("bucket", "prefix/key.json"))
        with self.assertRaises(ValueError):
            parse_s3_uri("https://example.com/file.json")


if __name__ == "__main__":
    unittest.main()
