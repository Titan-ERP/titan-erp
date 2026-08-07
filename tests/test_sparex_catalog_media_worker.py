import struct
import unittest
from pathlib import Path

from scripts.sparex_catalog_media_worker import image_metadata, s3_image_metadata


class SparexCatalogMediaWorkerTests(unittest.TestCase):
    def test_reads_png_dimensions_from_real_header(self):
        content = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480) + b"\x08\x02\x00\x00\x00"
        self.assertEqual(image_metadata(content), ("image/png", 640, 480))

    def test_rejects_non_image_content(self):
        with self.assertRaisesRegex(ValueError, "unsupported_or_invalid_image"):
            image_metadata(b"<html>not an image</html>")

    def test_s3_metadata_normalizes_odoo_false_to_string(self):
        self.assertEqual(
            s3_image_metadata({"image_source_sha256": False}, 640, 480),
            {"source-url-sha256": "", "width": "640", "height": "480"},
        )

    def test_worker_prioritizes_cost_evidenced_items(self):
        source = (Path(__file__).parents[1] / "scripts" / "sparex_catalog_media_worker.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('(\"vendor_cost\", \">\", 0)', source)
        self.assertIn('(\"dealer_cost_evidence_sha256\", \"!=\", False)', source)


if __name__ == "__main__":
    unittest.main()
