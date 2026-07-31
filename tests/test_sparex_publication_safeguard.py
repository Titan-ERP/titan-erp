from __future__ import annotations

import unittest

from scripts.odoo_sparex_publication_safeguard import publication_blockers


class SparexPublicationSafeguardTests(unittest.TestCase):
    def base_product(self):
        return {
            "list_price": 19.99,
            "southern_source_url": "https://us.sparex.com/example.html",
            "public_categ_ids": [1],
            "image_1920": "present",
            "description_ecommerce": "Customer-ready description",
            "description_sale": "",
        }

    def test_eligible_product_has_no_blockers(self):
        self.assertEqual(publication_blockers(self.base_product(), [10.0]), [])

    def test_zero_supplier_line_is_not_verified_cost(self):
        self.assertIn("missing_positive_supplier_cost", publication_blockers(self.base_product(), [0.0]))

    def test_price_must_exceed_supplier_cost(self):
        product = self.base_product()
        product["list_price"] = 10.0
        self.assertIn("sales_price_not_above_supplier_cost", publication_blockers(product, [10.0]))

    def test_source_must_be_exact_sparex_https_host(self):
        product = self.base_product()
        product["southern_source_url"] = "https://example.com/sparex"
        self.assertIn("missing_verified_sparex_source", publication_blockers(product, [10.0]))


if __name__ == "__main__":
    unittest.main()
