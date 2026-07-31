import unittest

try:
    from scripts.odoo_publish_qualified_brokerage_listings import publication_blockers
except ModuleNotFoundError:
    from odoo_publish_qualified_brokerage_listings import publication_blockers


class PublicationGateTests(unittest.TestCase):
    def record(self, **overrides):
        values = {
            "company_id": [2, "Southern Equipment Company (Laurel)"],
            "public_status": "seller_confirmed",
            "grade": "good",
            "comp_count": 1,
            "year": 2021,
            "model": "T66",
            "hours": 1200,
            "public_price": 52500,
            "public_region": "Mississippi",
            "public_description": "<p>Verified opportunity.</p>",
            "verification_note": "Availability confirmed.",
            "image_present": True,
            "photo_rights_confirmed": True,
            "photo_source_note": "Southern-owned representative image.",
            "source": "facebook_marketplace",
            "source_listing_id": "1362964311864158",
            "source_url": "https://www.facebook.com/marketplace/item/1362964311864158",
        }
        values.update(overrides)
        return values

    def test_complete_good_deal_is_eligible(self):
        self.assertEqual(publication_blockers(self.record()), [])

    def test_year_and_model_are_required_but_hours_are_optional(self):
        self.assertIn("missing_year", publication_blockers(self.record(year=0)))
        self.assertIn("missing_model", publication_blockers(self.record(model="")))
        self.assertNotIn("missing_hours", publication_blockers(self.record(hours=0)))

    def test_bad_source_is_blocked_but_missing_comps_are_advisory(self):
        self.assertIn(
            "source_link_mismatch",
            publication_blockers(
                self.record(source_url="https://www.facebook.com/marketplace/item/999")
            ),
        )
        self.assertEqual(publication_blockers(self.record(comp_count=0)), [])

    def test_valuation_grade_is_advisory(self):
        self.assertEqual(publication_blockers(self.record(grade="verify")), [])
        self.assertEqual(publication_blockers(self.record(grade="pass")), [])


if __name__ == "__main__":
    unittest.main()
