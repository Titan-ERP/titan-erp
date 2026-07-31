import unittest

from package_visible_facebook_cards import package


def card():
    return {
        "id": "123456789",
        "title": "2020 Caterpillar 279D3 Skid Steer",
        "standardized_title": "2020 Caterpillar 279D3 Compact Track Loader",
        "equipment_type": "Skid Steer",
        "manufacturer": "Caterpillar",
        "model": "279D3",
        "year": 2020,
        "price": 48000,
        "location": "Laurel, MS",
        "detail_verified": True,
        "detail_id": "123456789",
        "detail_title": "2020 Caterpillar 279D3 Skid Steer",
        "detail_price": 48000,
        "detail_location": "Laurel, MS",
        "detail_checked_at": "2026-07-26T18:30:00-05:00",
        "description": "Visible seller description.",
        "seller_name": "Visible Seller",
        "shared_url": "https://www.facebook.com/share/example/",
    }


class TestVisibleFacebookPackaging(unittest.TestCase):
    def test_matching_detail_page_packages(self):
        rows = package(
            [card()],
            "test-run",
            "2026-07-26T18:30:00-05:00",
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["Source Listing ID"], "123456789")
        self.assertEqual(
            rows[0]["Standardized Title"],
            "2020 Caterpillar 279D3 Compact Track Loader",
        )
        self.assertIn("Visible seller description.", rows[0]["Raw Capture Text"])
        self.assertEqual(rows[0]["Seller Name"], "Visible Seller")
        self.assertEqual(
            rows[0]["Original Facebook Link"],
            "https://www.facebook.com/share/example/",
        )

    def test_texas_location_is_supported(self):
        candidate = card()
        candidate["location"] = "Van Alstyne, TX"
        candidate["detail_location"] = "Van Alstyne, TX"
        rows = package(
            [candidate],
            "test-run",
            "2026-07-26T18:30:00-05:00",
        )
        self.assertEqual(rows[0]["Public Region"], "Texas")

    def test_missing_detail_verification_fails_closed(self):
        candidate = card()
        candidate["detail_verified"] = False
        with self.assertRaisesRegex(ValueError, "detail page was not verified"):
            package([candidate], "test-run", "2026-07-26T18:30:00-05:00")

    def test_detail_price_conflict_fails_closed(self):
        candidate = card()
        candidate["detail_price"] = 1
        with self.assertRaisesRegex(ValueError, "price conflicts"):
            package([candidate], "test-run", "2026-07-26T18:30:00-05:00")

    def test_detail_id_conflict_fails_closed(self):
        candidate = card()
        candidate["detail_id"] = "987654321"
        with self.assertRaisesRegex(ValueError, "listing ID conflict"):
            package([candidate], "test-run", "2026-07-26T18:30:00-05:00")


if __name__ == "__main__":
    unittest.main()
