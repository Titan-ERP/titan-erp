import tempfile
import unittest
from pathlib import Path

from normalize_manual_auction_comps import parse_file
from odoo_import_equipment_comps import values_from_row


class ManualAuctionCompImportTests(unittest.TestCase):
    def test_tracked_excavator_is_preserved(self):
        text = """2022 John Deere 210G LC Tracked Excavator
Lot 253
2022 John Deere 210G LC Tracked Excavator
Air Conditioner
Midland, TX
4,430 hr
Sold
$80,000
USD
Closed: Feb 11, 2026
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tracked.txt"
            path.write_text(text, encoding="utf-8")
            rows, rejected = parse_file(path)

        self.assertEqual([], rejected)
        self.assertEqual(1, len(rows))
        self.assertEqual("Tracked Excavator", rows[0]["category"])
        self.assertEqual("Ritchie Bros.", rows[0]["source_name"])
        self.assertEqual("John Deere", rows[0]["make"])
        self.assertEqual("210G LC", rows[0]["model"])

        values = values_from_row(rows[0], company_id=2)
        self.assertEqual("excavator", values["equipment_type"])
        self.assertEqual("Ritchie Bros.", values["source"])
        self.assertIn("Tracked Excavator", values["name"])

    def test_mini_excavator_remains_supported(self):
        text = """2023 Bobcat E35Z Mini Excavator
Lot 442
2023 Bobcat E35Z Mini Excavator
Lake Point, UT
1,200 hr
Sold
$42,000
USD
Closed: Jun 1, 2026
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mini.txt"
            path.write_text(text, encoding="utf-8")
            rows, rejected = parse_file(path)

        self.assertEqual([], rejected)
        values = values_from_row(rows[0], company_id=2)
        self.assertEqual("mini_excavator", values["equipment_type"])
        self.assertIn("Mini Excavator", values["name"])

    def test_inoperable_result_is_rejected(self):
        text = """2006 Hitachi ZX240LC-3 Tracked Excavator (Inoperable)
Lot 3670
2006 Hitachi ZX240LC-3 Tracked Excavator (Inoperable)
Jenkins, KY
Sold
$8,750
USD
Closed: Jul 15, 2026
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inoperable.txt"
            path.write_text(text, encoding="utf-8")
            rows, rejected = parse_file(path)

        self.assertEqual([], rows)
        self.assertTrue(
            any("Inoperable/salvage" in row["Reason"] for row in rejected)
        )

    def test_skid_steer_loader_is_classified_and_descriptor_removed(self):
        text = """2021 Bobcat S66 Two-Speed Skid Steer Loader
Lot 273
2021 Bobcat S66 Two-Speed Skid Steer Loader
Lake Worth, TX
2,410 hr
Sold
$16,000
USD
Closed: Jun 17, 2026
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "skid.txt"
            path.write_text(text, encoding="utf-8")
            rows, rejected = parse_file(path)

        self.assertEqual([], rejected)
        self.assertEqual("Skid Steer Loader", rows[0]["category"])
        self.assertEqual("S66", rows[0]["model"])
        values = values_from_row(rows[0], company_id=2)
        self.assertEqual("skid_steer", values["equipment_type"])

    def test_crawler_dozer_is_classified_and_lgp_removed(self):
        text = """2024 Cat D4 LGP Crawler Dozer
Lot 301
2024 Cat D4 LGP Crawler Dozer
Davenport, FL
3,084 hr
Sold
$143,000
USD
Closed: May 21, 2026
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dozer.txt"
            path.write_text(text, encoding="utf-8")
            rows, rejected = parse_file(path)

        self.assertEqual([], rejected)
        self.assertEqual("Crawler Dozer", rows[0]["category"])
        self.assertEqual("D4", rows[0]["model"])
        values = values_from_row(rows[0], company_id=2)
        self.assertEqual("dozer", values["equipment_type"])

    def test_unverified_year_and_repeated_whitespace_are_handled(self):
        text = """2019 (unverified) Komatsu D275AX-5E0 Crawler Dozer
Lot 77
2019  (unverified) Komatsu D275AX-5E0 Crawler Dozer
Newnan, GA
8,100 hr
Sold
$88,000
USD
Closed: Jun 17, 2026
"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unverified-year.txt"
            path.write_text(text, encoding="utf-8")
            rows, rejected = parse_file(path)

        self.assertEqual([], rejected)
        self.assertEqual("", rows[0]["year"])
        self.assertEqual("Year unverified", rows[0]["condition_note"])
        self.assertEqual("D275AX-5E0", rows[0]["model"])


if __name__ == "__main__":
    unittest.main()
