import hashlib
import io
import json
import struct
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import requests

from scripts.odoo_runtime.client import OdooError
from scripts.sparex_catalog_media_worker import (
    MEDIA_TRANSIENT_EXIT,
    MEDIA_UNKNOWN_EXIT,
    classify_media_failure,
    image_metadata,
    media_candidate_domain,
    s3_image_metadata,
)

ROOT = Path(__file__).resolve().parents[1]


def _http_error(status: int) -> requests.HTTPError:
    response = requests.Response()
    response.status_code = status
    return requests.HTTPError(f"{status} error", response=response)


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
        source = (ROOT / "scripts" / "sparex_catalog_media_worker.py").read_text(encoding="utf-8")
        self.assertIn('("vendor_cost", ">", 0)', source)
        self.assertIn('("dealer_cost_evidence_sha256", "!=", False)', source)
        self.assertIn("record_media_outcomes", source)
        self.assertNotIn("return 0 if not failures else 2", source)

    def test_classifies_permanent_and_transient_image_failures(self):
        self.assertEqual(classify_media_failure(ValueError("image_url_not_https")), ("permanent", "image_url_not_https"))
        self.assertEqual(classify_media_failure(_http_error(404)), ("permanent", "image_http_404"))
        self.assertEqual(classify_media_failure(_http_error(410)), ("permanent", "image_http_410"))
        self.assertEqual(classify_media_failure(_http_error(503)), ("transient", "image_http_503"))
        self.assertEqual(classify_media_failure(requests.Timeout("timed out")), ("transient", "image_network_timeout"))
        self.assertEqual(classify_media_failure(RuntimeError("contract exploded")), ("unknown", "unexpected_media_failure"))
        self.assertEqual(
            classify_media_failure(OdooError("Odoo JSON-2 503 ValueError: database timeout")),
            ("transient", "odoo_transient"),
        )
        self.assertEqual(
            classify_media_failure(OdooError("Odoo request failed: TimeoutError")),
            ("transient", "odoo_transient"),
        )
        client_error = type("ClientError", (Exception,), {})("s3 unavailable")
        self.assertEqual(classify_media_failure(client_error), ("transient", "media_infrastructure_transient"))

    def test_media_domain_skips_manual_review_and_future_retries(self):
        domain = media_candidate_domain()
        self.assertIn(("media_state", "not in", ["manual_review"]), domain)
        self.assertIn(("media_state", "!=", "retry_wait"), domain)
        self.assertIn("media_next_attempt_at", {term[0] for term in domain if isinstance(term, (list, tuple))})

    def test_one_http_404_does_not_fail_closed_the_batch(self):
        rows = [
            {
                "id": index + 1,
                "image_url": f"https://cdn.example.com/{index + 1}.jpg",
                "image_source_sha256": hashlib.sha256(f"https://cdn.example.com/{index + 1}.jpg".encode()).hexdigest(),
                "content_sha256": "a" * 64,
            }
            for index in range(25)
        ]
        applied = [{"item_id": row["id"], "status": "verified"} for row in rows if row["id"] != 7]

        def download(url: str):
            if url.endswith("/7.jpg"):
                raise _http_error(404)
            return b"image-bytes", "image/jpeg", 128, 128

        def call(model, method, **params):
            if method == "search_read":
                self.assertIn(("media_state", "not in", ["manual_review"]), params["domain"])
                return rows
            if method == "apply_media_batch":
                self.assertEqual(len(params["records"]), 24)
                self.assertNotIn(7, [record["item_id"] for record in params["records"]])
                return applied
            if method == "record_media_outcomes":
                self.assertEqual(params["records"], [
                    {
                        "item_id": 7,
                        "kind": "permanent",
                        "failure_class": "image_http_404",
                        "error_safe": "image_http_404",
                    }
                ])
                return [{"item_id": 7, "status": "manual_review"}]
            raise AssertionError((model, method, params))

        client = MagicMock()
        client.call.side_effect = call
        argv = [
            "media-worker",
            "--odoo-env-file",
            "odoo.env",
            "--s3-bucket",
            "test-bucket",
            "--limit",
            "25",
        ]
        s3 = MagicMock()
        stdout = io.StringIO()
        with (
            patch.object(sys, "argv", argv),
            patch("scripts.sparex_catalog_media_worker.OdooConfig.from_env", return_value=MagicMock()),
            patch("scripts.sparex_catalog_media_worker.OdooClient") as client_cls,
            patch("scripts.sparex_catalog_media_worker.download_image", side_effect=download),
            patch("scripts.sparex_catalog_media_worker.time.sleep"),
            patch.dict("sys.modules", {"boto3": MagicMock(client=MagicMock(return_value=s3))}),
            patch("sys.stdout", stdout),
        ):
            client_cls.return_value.connect.return_value = client
            from scripts.sparex_catalog_media_worker import main

            status = main()
        self.assertEqual(status, 0)
        written = json.loads(stdout.getvalue())
        self.assertEqual(written["processed"], 24)
        self.assertEqual(written["permanent_failures"], 1)
        self.assertEqual(written["transient_failures"], 0)
        self.assertEqual(written["unknown_failures"], 0)
        self.assertEqual(written["state"], "complete")

    def test_http_503_is_transient_and_not_a_portal_warning(self):
        self.assertEqual(classify_media_failure(_http_error(503))[0], "transient")
        source = (ROOT / "scripts" / "sparex_catalog_media_worker.py").read_text(encoding="utf-8")
        self.assertNotIn("return 75", source)
        self.assertIn("MEDIA_TRANSIENT_EXIT = 76", source)
        self.assertEqual(MEDIA_TRANSIENT_EXIT, 76)
        self.assertNotEqual(MEDIA_TRANSIENT_EXIT, 75)

    def test_unknown_exception_still_fails_closed(self):
        rows = [
            {
                "id": 9,
                "image_url": "https://cdn.example.com/9.jpg",
                "image_source_sha256": hashlib.sha256(b"https://cdn.example.com/9.jpg").hexdigest(),
                "content_sha256": "a" * 64,
            }
        ]

        def call(model, method, **params):
            if method == "search_read":
                return rows
            raise AssertionError((model, method, params))

        client = MagicMock()
        client.call.side_effect = call
        argv = ["media-worker", "--odoo-env-file", "odoo.env", "--s3-bucket", "test-bucket"]
        with (
            patch.object(sys, "argv", argv),
            patch("scripts.sparex_catalog_media_worker.OdooConfig.from_env", return_value=MagicMock()),
            patch("scripts.sparex_catalog_media_worker.OdooClient") as client_cls,
            patch(
                "scripts.sparex_catalog_media_worker.download_image",
                side_effect=RuntimeError("schema contract exploded"),
            ),
            patch("scripts.sparex_catalog_media_worker.time.sleep"),
            patch.dict("sys.modules", {"boto3": MagicMock(client=MagicMock())}),
        ):
            client_cls.return_value.connect.return_value = client
            from scripts.sparex_catalog_media_worker import main

            status = main()
        self.assertEqual(status, MEDIA_UNKNOWN_EXIT)


if __name__ == "__main__":
    unittest.main()
