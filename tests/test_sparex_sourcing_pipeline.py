from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.sparex_sourcing_pipeline import (
    apply_evidence,
    choose_exact_price,
    odoo_datetime,
    page_matches_sku,
    sha256_file,
    verify_explicit_input,
)


class SparexSourcingPipelineTests(unittest.TestCase):
    def test_odoo_datetime_uses_server_field_format(self):
        value = datetime(2026, 7, 31, 11, 32, 39, 273778, tzinfo=timezone.utc)
        self.assertEqual(odoo_datetime(value), "2026-07-31 11:32:39")

    def test_json_ld_exact_price_is_accepted(self):
        payload = {
            "@type": "Product",
            "sku": "S.12345",
            "offers": {"@type": "Offer", "price": "12.34", "priceCurrency": "USD"},
        }
        html = f'<script type="application/ld+json">{json.dumps(payload)}</script>'
        result = choose_exact_price(html, "S.12345")
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["price"], 12.34)

    def test_generic_dollar_amount_is_rejected(self):
        result = choose_exact_price("<p>Shipping $4.99</p>", "S.12345")
        self.assertEqual(result["status"], "no_exact_price")

    def test_conflicting_structured_prices_are_ambiguous(self):
        html = (
            '<meta itemprop="price" content="10.00">'
            '<script>{"final_price": 11.00}</script>'
        )
        self.assertEqual(choose_exact_price(html, "S.12345")["status"], "ambiguous_price")

    def test_page_requires_exact_sku_evidence(self):
        self.assertTrue(page_matches_sku("<h1>Part S.12345</h1>", "https://us.sparex.com/item.html", "S.12345"))
        self.assertFalse(page_matches_sku("<h1>Part S.99999</h1>", "https://us.sparex.com/item.html", "S.12345"))

    def test_explicit_input_hash_is_required(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "plan.json"
            path.write_text(json.dumps({"schema_version": "1.0"}), encoding="utf-8")
            payload = verify_explicit_input(path, sha256_file(path))
            self.assertEqual(payload["schema_version"], "1.0")
            with self.assertRaises(RuntimeError):
                verify_explicit_input(path, "0" * 64)

    def test_publish_command_rechecks_evidence_freshness(self):
        source = (Path(__file__).parents[1] / "scripts" / "sparex_sourcing_pipeline.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('(\"evidence_retrieved_at\", \">=\", evidence_cutoff)', source)

    def test_price_pending_apply_preserves_verified_url_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence_path = root / "evidence.json"
            evidence_path.write_text(
                json.dumps(
                    {
                        "schema_version": "1.0",
                        "stage": "evidence",
                        "run_id": "plan-test",
                        "parser_version": "browser-exact-price-v3",
                        "records": [
                            {
                                "product_id": 11406,
                                "supplier_id": 378,
                                "sku": "S.165554",
                                "status": "price_pending",
                                "url_verified": True,
                                "evidence_url": "https://us.sparex.com/oil-filter-spin-on-165554.html",
                                "evidence_sha256": "a" * 64,
                                "evidence_schema_version": "1.0",
                                "retrieved_at_utc": "2026-07-31T21:44:43.913Z",
                                "parser_version": "browser-exact-price-v3",
                                "failure_code": "price_pending",
                                "failure_message": "Exact page loaded without a dealer price.",
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            client = Mock()
            args = SimpleNamespace(
                input=evidence_path,
                input_sha256=sha256_file(evidence_path),
                apply=True,
                confirm="sparex-stage-evidence",
                reason="test",
                max_records=1,
                env_file=None,
            )

            with (
                patch("scripts.sparex_sourcing_pipeline.ApplyGate") as gate,
                patch("scripts.sparex_sourcing_pipeline.odoo_client", return_value=client),
                patch("scripts.sparex_sourcing_pipeline.append_audit"),
            ):
                result = apply_evidence(args)

            gate.return_value.authorize.assert_called_once_with(1)
            self.assertEqual(result["staged"], 1)
            values = client.call.call_args.kwargs["values"]
            self.assertEqual(values["evidence_url"], "https://us.sparex.com/oil-filter-spin-on-165554.html")
            self.assertEqual(values["evidence_sha256"], "a" * 64)
            self.assertEqual(values["evidence_retrieved_at"], "2026-07-31 21:44:43")
            self.assertEqual(values["parser_version"], "browser-exact-price-v3")
            self.assertEqual(values["failure_reason"], "Exact page loaded without a dealer price.")
            self.assertNotIn("supplier_price", values)


if __name__ == "__main__":
    unittest.main()
