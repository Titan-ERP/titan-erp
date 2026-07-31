import unittest
from datetime import date
from unittest.mock import patch

from equipment_comp_analysis import (
    Comp,
    analyze,
    comp_from_row,
    odoo_search_read_all,
    score_deal,
)


class TestEquipmentCompAnalysis(unittest.TestCase):
    def setUp(self):
        self.listing = {
            "Source Listing ID": "TEST-1",
            "Standardized Title": "2020 Caterpillar D5K2 Dozer",
            "Seller Ask": "60000",
            "Equipment Type": "Dozer",
            "Manufacturer": "Caterpillar",
            "Model": "D5K2",
            "Year": "2020",
            "Hours": "2000",
        }
        prices = [68000, 70000, 72000, 74000, 76000]
        self.comps = [
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=year,
                hours=hours,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/{index}",
            )
            for index, (price, year, hours) in enumerate(
                zip(prices, [2019, 2020, 2020, 2021, 2022], [2400, 2100, 1900, 1700, 1500])
            )
        ]

    def test_odoo_search_read_all_reads_every_batch(self):
        def fake_call(_connection, _model, method, args, kwargs=None):
            if method == "search":
                return list(range(1, 2506))
            self.assertEqual(method, "read")
            return [{"id": record_id} for record_id in args[0]]

        with patch("equipment_comp_analysis.odoo_call", side_effect=fake_call):
            rows = odoo_search_read_all(
                object(),
                "southern.equipment.comp",
                [("company_id", "=", 2)],
                ["id"],
                batch_size=1000,
            )
        self.assertEqual(len(rows), 2505)
        self.assertEqual(rows[0]["id"], 1)
        self.assertEqual(rows[-1]["id"], 2505)

    def test_good_deal_uses_only_comp_range(self):
        result = analyze(self.listing, self.comps)
        self.assertEqual(result["Comp Count"], 5)
        self.assertIn(result["Comp Confidence"], ("medium", "high"))
        self.assertEqual(result["Grade"], "strong")
        self.assertGreater(float(result["Deal Score"]), 70)
        self.assertIn("No freight", result["Method"])

    def test_insufficient_comps_remain_verify(self):
        result = analyze(self.listing, [])
        self.assertEqual(result["Grade"], "verify")
        self.assertEqual(result["Recommendation"], "Insufficient comparable evidence")

    def test_fewer_than_three_compatible_comps_is_insufficient(self):
        result = analyze(self.listing, self.comps[:1])
        self.assertEqual(result["Comp Count"], 0)
        self.assertEqual(result["Comp Confidence"], "low")
        self.assertEqual(result["Grade"], "verify")

    def test_missing_listing_hours_suppresses_range_without_rejecting_listing(self):
        result = analyze(dict(self.listing, Hours=""), self.comps)
        self.assertEqual(result["Comp Count"], 0)
        self.assertEqual(result["Comp Match Basis"], "insufficient")
        self.assertEqual(result["Grade"], "verify")
        self.assertIn("hours were not provided", result["Public Valuation Summary"])
        self.assertIn("listing remains available", result["Public Valuation Summary"])

    def test_deduplicates_sources_and_removes_robust_price_outlier(self):
        comps = list(self.comps)
        comps.append(
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=2020,
                hours=2000,
                price=200000,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url="https://example.test/extreme",
            )
        )
        comps.append(
            Comp(
                source="Authorized auction export duplicate",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=2019,
                hours=2400,
                price=68000,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=self.comps[0].source_url,
            )
        )
        result = analyze(self.listing, comps)
        self.assertEqual(result["Comp Count"], 5)
        self.assertEqual(float(result["Comp Median"]), 72000)

    def test_above_high_is_not_good(self):
        score, grade, discount = score_deal(90000, 68000, 72000, 76000, "high")
        self.assertEqual(grade, "pass")
        self.assertLess(score, 45)
        self.assertLess(discount, 0)

    def test_up_to_ten_percent_above_median_is_good(self):
        _score, grade, discount = score_deal(79200, 68000, 72000, 76000, "high")
        self.assertEqual(grade, "good")
        self.assertAlmostEqual(discount, -0.10)

    def test_more_than_ten_percent_above_median_is_not_good(self):
        _score, grade, discount = score_deal(79201, 68000, 72000, 82000, "high")
        self.assertEqual(grade, "verify")
        self.assertLess(discount, -0.10)

    def test_machinery_trader_requires_documented_data_rights(self):
        row = {
            "Source": "MachineryTrader",
            "Equipment Type": "Dozer",
            "Manufacturer": "Caterpillar",
            "Model": "D5K2",
            "Price": "75000",
            "Sale Type": "asking",
            "Currency": "USD",
        }
        self.assertIsNone(comp_from_row(row))
        row["Data Rights Confirmed"] = "Yes"
        self.assertIsNone(comp_from_row(row))
        row["Authorization Reference"] = "Southern Equipment license MT-EXAMPLE"
        self.assertIsNotNone(comp_from_row(row))

    def test_different_model_size_does_not_fallback_by_manufacturer(self):
        listing = dict(self.listing, Model="350G", Manufacturer="John Deere")
        comps = [
            Comp(
                source="Authorized auction export",
                equipment_type="Excavator",
                manufacturer="John Deere",
                model="35G",
                year=2023,
                hours=500,
                price=35000,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url="",
            )
            for _ in range(5)
        ]
        result = analyze(listing, comps)
        self.assertEqual(result["Comp Count"], 0)
        self.assertEqual(result["Grade"], "verify")

    def test_missing_make_or_model_never_matches_type_only(self):
        for missing in (
            dict(self.listing, Manufacturer=""),
            dict(self.listing, Model=""),
        ):
            result = analyze(missing, self.comps)
            self.assertEqual(result["Comp Count"], 0)
            self.assertEqual(result["Comp Median"], "0.00")
            self.assertEqual(result["Grade"], "verify")

    def test_cat_cr_suffix_is_compatible_model_family(self):
        listing = dict(
            self.listing,
            Model="305",
            Manufacturer="Caterpillar",
            Hours="500",
            **{"Equipment Type": "Excavator"},
        )
        comps = [
            Comp(
                source="Authorized auction export",
                equipment_type="Excavator",
                manufacturer="Caterpillar",
                model="305 CR",
                year=2023,
                hours=500,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url="",
            )
            for price in (35000, 37000, 39000)
        ]
        result = analyze(listing, comps)
        self.assertEqual(result["Comp Count"], 3)
        self.assertEqual(result["Comp Confidence"], "medium")

    def test_external_validator_refuses_cross_brand_without_spec_profiles(self):
        cross_brand = [
            Comp(
                source="Authorized auction export",
                equipment_type="Crawler Dozer",
                manufacturer=manufacturer,
                model=model,
                year=year,
                hours=hours,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/cross-{index}",
            )
            for index, (manufacturer, model, year, hours, price) in enumerate(
                [
                    ("John Deere", "650K", 2019, 1800, 68000),
                    ("Komatsu", "D51PX-24", 2020, 2100, 70000),
                    ("Case", "850M WT", 2021, 2200, 72000),
                ]
            )
        ]
        insufficient = analyze(self.listing, cross_brand[:2])
        self.assertEqual(insufficient["Comp Count"], 0)
        self.assertEqual(insufficient["Comp Match Basis"], "insufficient")

        result = analyze(self.listing, cross_brand)
        self.assertEqual(result["Comp Count"], 0)
        self.assertEqual(result["Comp Match Basis"], "insufficient")
        self.assertIn("not enough closely matched", result["Public Valuation Summary"])

    def test_cross_brand_never_mixes_with_primary_or_wrong_size_tier(self):
        cross_brand = [
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer=manufacturer,
                model=model,
                year=2020,
                hours=2000,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/peer-{index}",
            )
            for index, (manufacturer, model, price) in enumerate(
                [
                    ("John Deere", "650K", 68000),
                    ("Komatsu", "D51PX-24", 70000),
                    ("Case", "850M", 72000),
                    ("Caterpillar", "D8T", 150000),
                ]
            )
        ]
        primary = [
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=2020,
                hours=hours,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/primary-{index}",
            )
            for index, (hours, price) in enumerate(
                [(1800, 74000), (2000, 75000), (2200, 76000)]
            )
        ]
        result = analyze(self.listing, cross_brand + primary)
        self.assertEqual(result["Comp Count"], 3)
        self.assertEqual(result["Comp Match Basis"], "exact_model")
        self.assertEqual(float(result["Comp Median"]), 75000)

    def test_prefers_nearby_year_and_hours_when_available(self):
        listing = dict(
            self.listing,
            Manufacturer="Bobcat",
            Model="T590",
            Year="2020",
            Hours="1500",
            **{
                "Seller Ask": "18000",
                "Equipment Type": "Compact Track Loader",
            },
        )
        far_comps = [
            Comp(
                source="Authorized auction export",
                equipment_type="Compact Track Loader",
                manufacturer="Bobcat",
                model="T590",
                year=2014,
                hours=6200,
                price=12000,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/far-{index}",
            )
            for index in range(5)
        ]
        close_comps = [
            Comp(
                source="Authorized auction export",
                equipment_type="Compact Track Loader",
                manufacturer="Bobcat",
                model="T590",
                year=year,
                hours=hours,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/close-{index}",
            )
            for index, (year, hours, price) in enumerate(
                [(2019, 1700, 19000), (2020, 1450, 20000), (2021, 1300, 21000)]
            )
        ]
        result = analyze(listing, far_comps + close_comps)
        self.assertEqual(result["Comp Count"], 3)
        self.assertGreaterEqual(float(result["Comp Median"]), 19000)
        self.assertIn("hour proximity", result["Method"])

    def test_widens_from_five_hundred_to_one_thousand_hours_when_needed(self):
        listing = dict(self.listing, Hours="2000")
        comps = [
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=2020,
                hours=hours,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/hours-{index}",
            )
            for index, (hours, price) in enumerate(
                [(1000, 68000), (2000, 70000), (3000, 72000), (3001, 10000)]
            )
        ]
        comps.append(
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=2020,
                hours=None,
                price=10000,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url="https://example.test/missing-hours",
            )
        )
        result = analyze(listing, comps)
        self.assertEqual(result["Comp Count"], 3)
        self.assertEqual(float(result["Comp Low"]), 68000)
        self.assertEqual(float(result["Comp Median"]), 70000)
        self.assertEqual(float(result["Comp High"]), 72000)
        self.assertEqual(result["Comp Confidence"], "medium")
        self.assertIn("expanded from 500 to 1,000", result["Public Valuation Summary"])

    def test_excludes_comps_more_than_three_years_from_listing(self):
        listing = dict(self.listing, Year="2020", Hours="2000")
        comps = [
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=year,
                hours=2000,
                price=price,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url=f"https://example.test/year-{index}",
            )
            for index, (year, price) in enumerate(
                [(2017, 68000), (2020, 70000), (2023, 72000), (2016, 10000)]
            )
        ]
        comps.append(
            Comp(
                source="Authorized auction export",
                equipment_type="Dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=None,
                hours=2000,
                price=10000,
                sale_type="auction_result",
                sale_date=date(2025, 1, 1),
                source_url="https://example.test/missing-year",
            )
        )
        result = analyze(listing, comps)
        self.assertEqual(result["Comp Count"], 3)
        self.assertEqual(float(result["Comp Low"]), 68000)
        self.assertEqual(float(result["Comp Median"]), 70000)
        self.assertEqual(float(result["Comp High"]), 72000)

    def test_excludes_different_equipment_class(self):
        wrong_type = Comp(
            source="Authorized auction export",
            equipment_type="Excavator",
            manufacturer="Caterpillar",
            model="D5K2",
            year=2020,
            hours=2000,
            price=10000,
            sale_type="auction_result",
            sale_date=date(2025, 1, 1),
            source_url="https://example.test/wrong-type",
        )
        result = analyze(self.listing, self.comps[:3] + [wrong_type])
        self.assertEqual(result["Comp Count"], 3)


if __name__ == "__main__":
    unittest.main()
