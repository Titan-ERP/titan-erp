import struct
import unittest

from scripts.sparex_catalog_media_worker import image_metadata


class SparexCatalogMediaWorkerTests(unittest.TestCase):
    def test_reads_png_dimensions_from_real_header(self):
        content = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 640, 480) + b"\x08\x02\x00\x00\x00"
        self.assertEqual(image_metadata(content), ("image/png", 640, 480))

    def test_rejects_non_image_content(self):
        with self.assertRaisesRegex(ValueError, "unsupported_or_invalid_image"):
            image_metadata(b"<html>not an image</html>")


if __name__ == "__main__":
    unittest.main()
