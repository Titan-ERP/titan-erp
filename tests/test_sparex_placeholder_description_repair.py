import unittest

from scripts.odoo_repair_sparex_placeholder_descriptions import customer_copy


class SparexPlaceholderDescriptionRepairTests(unittest.TestCase):
    def test_customer_copy_is_factual_and_escapes_product_data(self):
        values = customer_copy('Top Link <Pin> 3/4"', "S.74")
        self.assertIn("Top Link &lt;Pin&gt;", values["description_ecommerce"])
        self.assertIn("Sparex reference S.74", values["description_ecommerce"])
        self.assertNotIn("internal catalog record", values["description_ecommerce"].casefold())
        self.assertIn("Top Link <Pin>", values["description_sale"])


if __name__ == "__main__":
    unittest.main()
