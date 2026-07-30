import unittest

from odoo_refresh_equipment_comp_ranges import build_update, comparable


class TestAutomaticCompRefresh(unittest.TestCase):
    def test_eligible_range_updates_internal_comp_fields(self):
        update = build_update(
            {
                "Comp Low": "32000.00",
                "Comp Median": "35000.00",
                "Comp High": "39000.00",
                "Comp Count": 5,
                "Comp Confidence": "medium",
                "Comp Match Basis": "exact_model",
                "Public Valuation Summary": "Market comparison.",
                "Deal Score": "82.5",
                "Grade": "good",
            }
        )
        self.assertEqual(update["comp_median"], 35000.0)
        self.assertEqual(update["comp_count"], 5)
        self.assertEqual(update["comp_confidence"], "medium")
        self.assertNotIn("public_price", update)
        self.assertNotIn("website_published", update)
        self.assertEqual(update["public_deal_summary"], "Market comparison.")

    def test_zero_comps_clears_stale_range(self):
        update = build_update(
            {
                "Comp Low": "30000.00",
                "Comp Median": "35000.00",
                "Comp High": "40000.00",
                "Comp Count": 0,
                "Comp Confidence": "low",
                "Comp Match Basis": "insufficient",
                "Public Valuation Summary": "Comparable valuation is pending.",
                "Deal Score": "40.0",
                "Grade": "verify",
            }
        )
        self.assertEqual(update["comp_count"], 0)
        self.assertEqual(update["comp_median"], 0.0)
        self.assertEqual(update["estimated_market_value"], 0.0)
        self.assertEqual(update["grade"], "verify")

    def test_two_comps_are_insufficient(self):
        update = build_update(
            {
                "Comp Low": "30000.00",
                "Comp Median": "35000.00",
                "Comp High": "40000.00",
                "Comp Count": 2,
                "Comp Confidence": "medium",
                "Comp Match Basis": "exact_model",
                "Public Valuation Summary": "Insufficient evidence.",
                "Deal Score": "80.0",
                "Grade": "good",
            }
        )
        self.assertEqual(update["comp_count"], 2)
        self.assertEqual(update["comp_median"], 0.0)
        self.assertEqual(update["comp_confidence"], "low")
        self.assertEqual(update["grade"], "verify")

    def test_comparable_ignores_equal_numeric_values(self):
        self.assertEqual(
            comparable(
                {"comp_low": 32000, "comp_count": 4},
                {"comp_low": 32000.0, "comp_count": 4},
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
