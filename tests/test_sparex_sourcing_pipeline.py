from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from scripts.sparex_sourcing_pipeline import (
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


if __name__ == "__main__":
    unittest.main()
