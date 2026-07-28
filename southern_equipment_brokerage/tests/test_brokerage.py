import base64
import csv
import io

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import HttpCase, TransactionCase, tagged


TEST_IMAGE = (
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    b"+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@tagged("southern_brokerage", "post_install", "-at_install")
class TestSouthernEquipmentBrokerage(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Listing = cls.env["southern.equipment.listing"]
        cls.Comp = cls.env["southern.equipment.comp"]
        cls.SpecProfile = cls.env["southern.equipment.spec.profile"]
        cls.ModelAlias = cls.env["southern.equipment.model.alias"]
        cls.Inquiry = cls.env["southern.buyer.inquiry"]
        cls.seller = cls.env["res.partner"].create(
            {"name": "Private Test Seller", "phone": "555-0100"}
        )
        inspector_group = cls.env.ref(
            "southern_equipment_brokerage.group_southern_inspector_coordinator"
        )
        cls.inspector = cls.env["res.users"].create(
            {
                "name": "Brokerage Test Inspector",
                "login": "brokerage-test-inspector",
                "email": "inspector@example.com",
                "group_ids": [Command.set(inspector_group.ids)],
            }
        )

    def _listing_values(self, **overrides):
        values = {
            "public_title": "2022 Test Skid Steer",
            "public_status": "needs_verification",
            "public_region": "Southeast",
            "equipment_type": "skid_steer",
            "manufacturer": "Test Make",
            "model": "T100",
            "year": 2022,
            "hours": 1200,
            "source": "manual",
            "source_seller_id": self.seller.id,
            "seller_phone": "555-0100",
            "source_url": "https://internal.example/listing/1",
            "raw_capture_text": "Internal source text",
            "verification_note": "Availability is being verified by Southern Equipment.",
            "image_1920": TEST_IMAGE,
            "photo_rights_confirmed": True,
            "photo_source_note": "Owned test fixture.",
        }
        values.update(overrides)
        return values

    def _comp_values(self, **overrides):
        values = {
            "name": "Authorized T100 Auction Comp",
            "company_id": self.env.company.id,
            "source": "Authorized manual auction result",
            "equipment_type": "skid_steer",
            "manufacturer": "Test Make",
            "model": "T100",
            "year": 2022,
            "hours": 1300,
            "price": 48000,
            "sale_type": "auction_result",
            "sale_date": fields.Date.today(),
        }
        values.update(overrides)
        return values

    def test_native_comp_analysis_requires_three_compatible_comps(self):
        listing = self.Listing.create(
            self._listing_values(seller_ask_price=50000)
        )
        self.Comp.create(
            [
                self._comp_values(name=f"Compatible Comp {index}")
                for index in range(3)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_confidence, "medium")
        self.assertEqual(listing.comp_median, 48000)
        self.assertEqual(listing.estimated_market_value, 48000)
        self.assertEqual(listing.grade, "good")
        self.assertTrue(listing.comp_last_calculated_at)
        self.assertIn("1000-hours", listing.comp_method_version)

    def test_listing_input_changes_always_recalculate_native_comps(self):
        self.Comp.create(
            [
                self._comp_values(name=f"Automatic Recalc Comp {index}")
                for index in range(3)
            ]
        )
        listing = self.Listing.create(
            self._listing_values(seller_ask_price=45000)
        )

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_median, 48000)
        self.assertEqual(listing.grade, "good")

        listing.seller_ask_price = 60000

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_median, 48000)
        self.assertEqual(listing.grade, "pass")
        self.assertEqual(listing.valuation_readiness, "ready")

    def test_native_missing_hours_suppresses_range_but_keeps_listing_available(self):
        listing = self.Listing.create(
            self._listing_values(hours=0, seller_ask_price=50000)
        )
        self.Comp.create(self._comp_values())

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 0)
        self.assertEqual(listing.comp_match_basis, "insufficient")
        self.assertEqual(listing.grade, "verify")
        self.assertIn("hours were not provided", listing.public_deal_summary)
        self.assertIn("listing remains available", listing.public_deal_summary)
        self.assertFalse(listing.website_published)
        self.assertEqual(listing.valuation_readiness, "missing_hours")
        self.assertEqual(listing.comp_hours_window, "none")

    def test_native_comp_analysis_excludes_outside_hours_and_year_windows(self):
        listing = self.Listing.create(
            self._listing_values(seller_ask_price=45000)
        )
        self.Comp.create(
            [
                self._comp_values(),
                self._comp_values(name="Valid Comp Two"),
                self._comp_values(name="Valid Comp Three"),
                self._comp_values(
                    name="Too Many Hours",
                    hours=2201,
                    price=10000,
                    source_url="https://example.test/hours",
                ),
                self._comp_values(
                    name="Too Many Years",
                    year=2018,
                    hours=1200,
                    price=10000,
                    source_url="https://example.test/year",
                ),
                self._comp_values(
                    name="Missing Hours",
                    hours=0,
                    price=10000,
                    source_url="https://example.test/missing-hours",
                ),
                self._comp_values(
                    name="Missing Year",
                    year=0,
                    price=10000,
                    source_url="https://example.test/missing-year",
                ),
                self._comp_values(
                    name="Wrong Equipment Class",
                    equipment_type="excavator",
                    price=10000,
                    source_url="https://example.test/wrong-type",
                ),
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_median, 48000)

    def test_native_comp_analysis_never_matches_missing_identity(self):
        listing = self.Listing.create(
            self._listing_values(
                manufacturer=False,
                seller_ask_price=20000,
                comp_median=99999,
                deal_score=99,
                grade="strong",
            )
        )
        self.Comp.create(self._comp_values())

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 0)
        self.assertEqual(listing.comp_median, 0)
        self.assertEqual(listing.deal_score, 0)
        self.assertEqual(listing.grade, "verify")
        self.assertEqual(listing.valuation_readiness, "missing_identity")

    def test_reviewed_model_alias_maps_only_supported_equivalent_names(self):
        self.ModelAlias.create(
            {
                "equipment_type": "skid_steer",
                "manufacturer": "Test Make",
                "alias": "T-100 Series II",
                "canonical_model": "T100-2",
                "source_note": "Authorized manufacturer model bulletin fixture",
            }
        )
        listing = self.Listing.create(
            self._listing_values(
                model="T-100 Series II",
                seller_ask_price=45000,
            )
        )
        self.Comp.create(
            [
                self._comp_values(
                    name=f"Canonical Alias Comp {index}",
                    model="T100-2",
                    source_url=f"https://example.test/alias/{index}",
                )
                for index in range(3)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_match_basis, "exact_model")
        self.assertEqual(listing.valuation_readiness, "ready")

    def test_material_configuration_mismatch_is_excluded(self):
        listing = self.Listing.create(
            self._listing_values(
                equipment_type="dozer",
                manufacturer="Test Make",
                model="D100",
                valuation_configuration="LGP",
                seller_ask_price=50000,
            )
        )
        self.Comp.create(
            [
                self._comp_values(
                    name=f"Wrong XL Configuration {index}",
                    equipment_type="dozer",
                    model="D100",
                    valuation_configuration="XL",
                    source_url=f"https://example.test/config/{index}",
                )
                for index in range(3)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 0)
        self.assertEqual(listing.valuation_readiness, "insufficient_comps")

    def test_stale_comps_are_excluded_and_freshness_is_disclosed(self):
        listing = self.Listing.create(
            self._listing_values(seller_ask_price=45000)
        )
        today = fields.Date.today()
        self.Comp.create(
            [
                self._comp_values(
                    name=f"Current Comp {index}",
                    source_url=f"https://example.test/current/{index}",
                    sale_date=today,
                )
                for index in range(3)
            ]
            + [
                self._comp_values(
                    name="Stale Five-Year Comp",
                    source_url="https://example.test/stale",
                    sale_date=fields.Date.subtract(today, years=6),
                    price=999999,
                )
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_freshness, "current")
        self.assertEqual(listing.comp_hours_window, "tight_500")
        self.assertIn("within 3 model years and 500 hours", listing.public_deal_summary)
        self.assertIn("current freshness", listing.public_deal_summary)

    def test_comps_are_fresh_through_three_calendar_years(self):
        listing = self.Listing.create(
            self._listing_values(seller_ask_price=45000)
        )
        today = fields.Date.today()
        oldest_allowed = fields.Date.subtract(today, years=3)
        too_old = fields.Date.subtract(oldest_allowed, days=1)
        self.Comp.create(
            [
                self._comp_values(
                    name=f"Three-Year Comp {index}",
                    source_url=f"https://example.test/three-year/{index}",
                    sale_date=oldest_allowed,
                )
                for index in range(3)
            ]
            + [
                self._comp_values(
                    name="Three Years Plus One Day",
                    source_url="https://example.test/three-year/expired",
                    sale_date=too_old,
                    price=999999,
                )
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_freshness, "aging")
        self.assertEqual(listing.comp_oldest_sale_date, oldest_allowed)
        self.assertEqual(listing.comp_newest_sale_date, oldest_allowed)
        self.assertIn("aging freshness", listing.public_deal_summary)

    def test_completed_sale_records_aggregate_backtest_error(self):
        listing = self.Listing.create(
            self._listing_values(
                comp_median=50000,
                actual_sale_price=40000,
            )
        )

        listing.write({"public_status": "sold"})

        self.assertEqual(listing.valuation_at_sale, 50000)
        self.assertEqual(listing.valuation_error_pct, 25.0)
        self.assertEqual(listing.valuation_absolute_error_pct, 25.0)
        self.assertEqual(listing.valuation_accuracy, "outside_20")

    def test_native_comp_analysis_counts_compatible_cat_cr_family_for_confidence(self):
        listing = self.Listing.create(
            self._listing_values(
                manufacturer="Caterpillar",
                model="305",
                year=2024,
                hours=500,
                seller_ask_price=40000,
            )
        )
        self.Comp.create(
            [
                self._comp_values(
                    name=f"CAT 305 CR Comp {index}",
                    manufacturer="Caterpillar",
                    model="305 CR",
                    year=2023,
                    hours=500,
                    price=40000 + index * 1000,
                    condition_grade="good",
                )
                for index in range(6)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 6)
        self.assertEqual(listing.comp_confidence, "high")

    def test_native_comp_analysis_uses_all_exact_models_before_family_comps(self):
        listing = self.Listing.create(
            self._listing_values(
                manufacturer="Caterpillar",
                model="305",
                year=2024,
                hours=500,
                seller_ask_price=40000,
            )
        )
        self.Comp.create(
            [
                self._comp_values(
                    name=f"Exact CAT 305 Comp {index}",
                    manufacturer="Caterpillar",
                    model="305",
                    year=2023,
                    hours=500 + index * 50,
                    price=40000 + index * 1000,
                    source_url=f"https://example.test/exact-305/{index}",
                )
                for index in range(4)
            ]
            + [
                self._comp_values(
                    name=f"Family CAT 305 CR Comp {index}",
                    manufacturer="Caterpillar",
                    model="305 CR",
                    year=2023,
                    hours=500 + index * 50,
                    price=50000 + index * 1000,
                    source_url=f"https://example.test/family-305/{index}",
                )
                for index in range(3)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 4)
        self.assertEqual(listing.comp_match_basis, "exact_model")
        self.assertIn("native-v8-exact-model-first", listing.comp_method_version)

    def test_native_cross_brand_requires_three_documented_specification_peers(self):
        listing_profile = self.SpecProfile.create(
            {
                "equipment_type": "dozer",
                "manufacturer": "Caterpillar",
                "model": "D5K2",
                "operating_weight_lb": 21000,
                "horsepower": 105,
                "undercarriage": "tracked",
                "source_note": "Authorized manufacturer specification fixture",
            }
        )
        listing = self.Listing.create(
            self._listing_values(
                equipment_type="dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=2020,
                hours=2000,
                seller_ask_price=65000,
                spec_profile_id=listing_profile.id,
            )
        )
        peer_values = [
            ("John Deere", "650K", 2019, 1800, 68000),
            ("Komatsu", "D51PX-24", 2020, 2100, 70000),
            ("Case", "850M WT", 2021, 2200, 72000),
        ]
        peer_records = []
        for index, (manufacturer, model, year, hours, price) in enumerate(peer_values):
            profile = self.SpecProfile.create(
                {
                    "equipment_type": "dozer",
                    "manufacturer": manufacturer,
                    "model": model,
                    "operating_weight_lb": 20500 + index * 250,
                    "horsepower": 103 + index,
                    "undercarriage": "tracked",
                    "source_note": "Authorized manufacturer specification fixture",
                }
            )
            peer_records.append(
                self._comp_values(
                    name=f"Cross Brand Peer {index}",
                    equipment_type="dozer",
                    manufacturer=manufacturer,
                    model=model,
                    year=year,
                    hours=hours,
                    price=price,
                    spec_profile_id=profile.id,
                )
            )
        peers = self.Comp.create(peer_records)

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_match_basis, "cross_brand_peer")
        self.assertEqual(listing.comp_confidence, "medium")
        self.assertIn("cross-brand specification", listing.public_deal_summary)
        self.assertIn("not an appraisal", listing.public_deal_summary)

        peers[0].unlink()
        listing.action_recalculate_comp_analysis()
        self.assertEqual(listing.comp_count, 0)
        self.assertEqual(listing.comp_match_basis, "insufficient")
        self.assertIn("not enough closely matched", listing.public_deal_summary)

    def test_native_cross_brand_rejects_specification_size_mismatch(self):
        listing_profile = self.SpecProfile.create(
            {
                "equipment_type": "dozer",
                "manufacturer": "Test Make",
                "model": "Small 100",
                "operating_weight_lb": 20000,
                "horsepower": 100,
                "source_note": "Authorized specification fixture",
            }
        )
        large_profile = self.SpecProfile.create(
            {
                "equipment_type": "dozer",
                "manufacturer": "Other Make",
                "model": "Large 900",
                "operating_weight_lb": 70000,
                "horsepower": 300,
                "source_note": "Authorized specification fixture",
            }
        )
        listing = self.Listing.create(
            self._listing_values(
                equipment_type="dozer",
                manufacturer="Test Make",
                model="Small 100",
                spec_profile_id=listing_profile.id,
            )
        )
        self.Comp.create(
            [
                self._comp_values(
                    name=f"Wrong Size Peer {index}",
                    equipment_type="dozer",
                    manufacturer="Other Make",
                    model="Large 900",
                    spec_profile_id=large_profile.id,
                )
                for index in range(3)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 0)
        self.assertEqual(listing.comp_match_basis, "insufficient")

    def test_native_same_make_successor_model_can_use_documented_specs(self):
        listing_profile = self.SpecProfile.create(
            {
                "equipment_type": "skid_steer",
                "manufacturer": "Kubota",
                "model": "SVL75",
                "operating_weight_lb": 9000,
                "horsepower": 74,
                "rated_capacity_lb": 2300,
                "undercarriage": "tracked",
                "source_note": "Authorized specification fixture",
            }
        )
        comp_profile = self.SpecProfile.create(
            {
                "equipment_type": "skid_steer",
                "manufacturer": "Kubota",
                "model": "SVL75-2",
                "operating_weight_lb": 9039,
                "horsepower": 74.3,
                "rated_capacity_lb": 2300,
                "undercarriage": "tracked",
                "source_note": "Authorized specification fixture",
            }
        )
        listing = self.Listing.create(
            self._listing_values(
                manufacturer="Kubota",
                model="SVL75",
                year=2020,
                hours=3000,
                spec_profile_id=listing_profile.id,
            )
        )
        self.Comp.create(
            [
                self._comp_values(
                    name=f"SVL75-2 Successor {index}",
                    manufacturer="Kubota",
                    model="SVL75-2",
                    year=2020,
                    hours=2800 + index * 100,
                    spec_profile_id=comp_profile.id,
                    source_url=f"https://example.test/svl75/{index}",
                )
                for index in range(3)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_match_basis, "same_make_family")

    def test_native_same_make_documented_lineage_allows_successor_rating_change(self):
        listing_profile = self.SpecProfile.create(
            {
                "equipment_type": "mini_excavator",
                "manufacturer": "Caterpillar",
                "model": "303.5E CR",
                "operating_weight_lb": 7734,
                "horsepower": 31.6,
                "undercarriage": "tracked",
                "documented_lineage": "303.5 E-Series",
                "source_note": "Authorized Caterpillar lineage fixture",
            }
        )
        comp_profile = self.SpecProfile.create(
            {
                "equipment_type": "mini_excavator",
                "manufacturer": "Caterpillar",
                "model": "303.5E2 CR",
                "operating_weight_lb": 8209,
                "horsepower": 23.5,
                "undercarriage": "tracked",
                "documented_lineage": "303.5 E-Series",
                "source_note": "Authorized Caterpillar lineage fixture",
            }
        )
        listing = self.Listing.create(
            self._listing_values(
                equipment_type="mini_excavator",
                manufacturer="Caterpillar",
                model="303.5E CR",
                year=2016,
                hours=2800,
                spec_profile_id=listing_profile.id,
            )
        )
        self.Comp.create(
            [
                self._comp_values(
                    name=f"303.5E2 CR Successor {index}",
                    equipment_type="mini_excavator",
                    manufacturer="Caterpillar",
                    model="303.5E2 CR",
                    year=2016,
                    hours=2600 + index * 100,
                    spec_profile_id=comp_profile.id,
                    source_url=f"https://example.test/3035e2/{index}",
                )
                for index in range(3)
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_match_basis, "same_make_family")

    def test_comp_audit_explains_condition_and_hour_rejections(self):
        listing = self.Listing.create(
            self._listing_values(seller_ask_price=45000)
        )
        included = self.Comp.create(
            [
                self._comp_values(
                    name=f"Audit Included {index}",
                    source_url=f"https://example.test/included/{index}",
                    condition_grade="good",
                )
                for index in range(3)
            ]
        )
        salvage = self.Comp.create(
            self._comp_values(
                name="Audit Salvage",
                source_url="https://example.test/salvage",
                condition_grade="salvage",
            )
        )
        far_hours = self.Comp.create(
            self._comp_values(
                name="Audit Far Hours",
                source_url="https://example.test/far-hours",
                hours=2500,
            )
        )

        listing.action_view_comp_audit()
        audit = self.env["southern.equipment.comp.audit.line"].search(
            [("listing_id", "=", listing.id), ("user_id", "=", self.env.user.id)]
        )

        self.assertEqual(
            set(audit.filtered("included").mapped("comp_id").ids),
            set(included.ids),
        )
        self.assertIn(
            "inoperable or salvage",
            audit.filtered(lambda line: line.comp_id == salvage).reason,
        )
        self.assertIn(
            "more than 1,000 hours",
            audit.filtered(lambda line: line.comp_id == far_hours).reason,
        )

    def test_sold_listing_snapshots_valuation_and_backtests_actual_price(self):
        listing = self.Listing.create(
            self._listing_values(comp_median=50000, actual_sale_price=47500)
        )

        listing.write({"public_status": "sold"})

        self.assertEqual(listing.valuation_at_sale, 50000)
        self.assertEqual(listing.valuation_accuracy, "within_10")
        self.assertAlmostEqual(listing.valuation_error_pct, 5.263, places=2)

    def test_native_primary_comp_excludes_cross_brand_and_wrong_size_tier(self):
        listing = self.Listing.create(
            self._listing_values(
                equipment_type="dozer",
                manufacturer="Caterpillar",
                model="D5K2",
                year=2020,
                hours=2000,
                seller_ask_price=65000,
            )
        )
        self.Comp.create(
            [
                *[
                    self._comp_values(
                        name=f"Exact Primary {index}",
                        equipment_type="dozer",
                        manufacturer="Caterpillar",
                        model="D5K2",
                        year=2020,
                        hours=hours,
                        price=price,
                    )
                    for index, (hours, price) in enumerate(
                        [(1800, 74000), (2000, 75000), (2200, 76000)]
                    )
                ],
                self._comp_values(
                    name="Wrong Size",
                    equipment_type="dozer",
                    manufacturer="Caterpillar",
                    model="D8T",
                    year=2020,
                    hours=2000,
                    price=150000,
                ),
                *[
                    self._comp_values(
                        name=f"Cross Brand Peer {index}",
                        equipment_type="dozer",
                        manufacturer=manufacturer,
                        model=model,
                        year=2020,
                        hours=2000,
                        price=price,
                    )
                    for index, (manufacturer, model, price) in enumerate(
                        [
                            ("John Deere", "650K", 68000),
                            ("Komatsu", "D51PX-24", 70000),
                            ("Case", "850M", 72000),
                        ]
                    )
                ],
            ]
        )

        listing.action_recalculate_comp_analysis()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(listing.comp_match_basis, "exact_model")
        self.assertEqual(listing.comp_median, 75000)

    def test_public_comp_cards_use_only_selected_d39px_evidence_pool(self):
        listing = self.Listing.create(
            self._listing_values(
                equipment_type="dozer",
                manufacturer="Komatsu",
                model="D39PX-24",
                year=2019,
                hours=5600,
                seller_ask_price=57000,
            )
        )
        selected = self.Comp.create(
            [
                self._comp_values(
                    name=f"D39PX Selected {index}",
                    equipment_type="dozer",
                    manufacturer="Komatsu",
                    model="D39PX-24",
                    year=year,
                    hours=hours,
                    price=price,
                )
                for index, (year, hours, price) in enumerate(
                    [
                        (2018, 5200, 48000),
                        (2019, 5600, 50000),
                        (2020, 6000, 57500),
                    ]
                )
            ]
        )
        rejected = self.Comp.create(
            [
                self._comp_values(
                    name=f"Wrong Komatsu Dozer {model}",
                    equipment_type="dozer",
                    manufacturer="Komatsu",
                    model=model,
                    year=2019,
                    hours=5600,
                    price=price,
                )
                for model, price in [
                    ("D51PX-24", 80000),
                    ("D61PX-23", 95000),
                    ("D65", 120000),
                    ("D85", 175000),
                ]
            ]
        )

        listing.action_recalculate_comp_analysis()
        public_comps = listing.get_public_comp_records()

        self.assertEqual(listing.comp_count, 3)
        self.assertEqual(set(public_comps.ids), set(selected.ids))
        self.assertFalse(public_comps & rejected)

    def test_batch_slugs_are_unique_and_publish_requires_curated_fields(self):
        listings = self.Listing.create(
            [self._listing_values(), self._listing_values()]
        )
        self.assertEqual(len(set(listings.mapped("public_slug"))), 2)
        listings[0].action_publish()
        self.assertTrue(listings[0].website_published)
        with self.assertRaises(ValidationError):
            self.Listing.create(
                self._listing_values(public_region=False, website_published=True)
            )
        with self.assertRaises(ValidationError):
            self.Listing.create(
                self._listing_values(verification_note=False, website_published=True)
            )
        with self.assertRaises(ValidationError):
            self.Listing.create(
                self._listing_values(image_1920=False, website_published=True)
            )
        with self.assertRaises(ValidationError):
            self.Listing.create(
                self._listing_values(
                    photo_rights_confirmed=False,
                    website_published=True,
                )
            )
        with self.assertRaises(ValidationError):
            self.Listing.create(
                self._listing_values(photo_source_note=False, website_published=True)
            )
        representative = self.Listing.create(
            self._listing_values(
                public_title="Representative Image Test",
                image_is_representative=True,
            )
        )
        representative.action_publish()
        self.assertTrue(representative.website_published)
        self.assertTrue(representative.image_is_representative)

    def test_facebook_photos_require_documented_reuse_permission(self):
        listing = self.Listing.create(
            self._listing_values(image_is_representative=True)
        )
        source_photo = self.env["ir.attachment"].create(
            {
                "name": "facebook-source-photo.png",
                "datas": TEST_IMAGE,
                "mimetype": "image/png",
            }
        )
        listing.facebook_source_photo_ids = [Command.set(source_photo.ids)]

        with self.assertRaises(UserError):
            listing.action_use_approved_facebook_photos()

        listing.facebook_photo_rights_confirmed = True
        with self.assertRaises(UserError):
            listing.action_use_approved_facebook_photos()

        listing.facebook_photo_rights_note = (
            "Seller authorized website reuse by email on 2026-07-27."
        )
        listing.action_use_approved_facebook_photos()

        self.assertIn(source_photo, listing.photo_ids)
        self.assertTrue(listing.image_1920)
        self.assertTrue(listing.photo_rights_confirmed)
        self.assertEqual(
            listing.photo_source_note,
            listing.facebook_photo_rights_note,
        )
        self.assertFalse(listing.image_is_representative)

    def test_public_and_inspector_cannot_read_sensitive_fields(self):
        listing = self.Listing.create(self._listing_values())
        public_user = self.env.ref("base.public_user")
        with self.assertRaises(AccessError):
            listing.with_user(public_user).read(["public_title"])
        with self.assertRaises(AccessError):
            listing.with_user(self.inspector).read(["seller_phone"])
        safe = listing.with_user(self.inspector).read(
            ["public_title", "public_region", "equipment_type"]
        )[0]
        self.assertEqual(safe["public_title"], listing.public_title)
        inquiry = self.Inquiry.create(
            {
                "listing_id": listing.id,
                "contact_name": "Private Buyer",
                "phone": "555-0198",
                "email": "private-buyer@example.com",
            }
        )
        with self.assertRaises(AccessError):
            inquiry.with_user(self.inspector).read(["contact_name"])

    def test_facebook_agent_import_is_private_and_unpublished(self):
        headers = [
            "Opportunity", "Customer", "Contact Name", "Phone", "Email",
            "Seller Facebook", "Stage", "Expected Revenue", "Priority", "Source",
            "Equipment ID", "Capture Run ID", "Equipment Name", "Equipment Type",
            "Manufacturer", "Model", "Year", "Hours", "VIN/Serial", "Ask Price",
            "Max Offer", "Projected Profit", "Margin %", "Location",
            "Facebook URL", "Internal Notes",
        ]
        row = [
            "Test opportunity", "Marketplace Seller", "Seller Name", "555-0111",
            "seller@example.com", "https://facebook.example/seller/1", "Qualified",
            "62000", "3", "Facebook Marketplace", "FB-TEST-001",
            "RUN-TEST-001", "2021 Test Compact Track Loader",
            "Loader", "Test Make", "CTL100", "2021", "3554", "",
            "42000", "39000", "20000", "32", "Exact City, MS",
            "https://facebook.com/marketplace/item/1/?tracking=abc",
            "Grade: Strong Buy | Captured text: original marketplace text",
        ]
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerow(row)
        wizard = self.env["southern.equipment.import.wizard"].create(
            {
                "upload_file": base64.b64encode(stream.getvalue().encode()),
                "upload_filename": "odoo-equipment-opportunities.csv",
                "validate_only": True,
            }
        )
        validation_action = wizard.action_import()
        self.assertEqual(validation_action["tag"], "display_notification")
        self.assertIn("1 new", validation_action["params"]["message"])
        self.assertFalse(
            self.Listing.search([("source_listing_id", "=", "FB-TEST-001")])
        )
        wizard.validate_only = False
        import_action = wizard.action_import()
        self.assertEqual(import_action["tag"], "display_notification")
        self.assertIn("1 new", import_action["params"]["message"])
        self.assertEqual(
            import_action["params"]["next"]["views"],
            [(False, "list"), (False, "form")],
        )
        listing = self.Listing.search(
            [("source_listing_id", "=", "FB-TEST-001")], limit=1
        )
        self.assertTrue(listing)
        self.assertFalse(listing.website_published)
        self.assertFalse(listing.public_region)
        self.assertFalse(listing.vin_serial)
        self.assertEqual(listing.hours, 3554)
        self.assertEqual(listing.equipment_type, "compact_track_loader")
        self.assertEqual(listing.capture_run_id, "RUN-TEST-001")
        self.assertEqual(
            listing.seller_facebook, "https://facebook.example/seller/1"
        )
        self.assertEqual(
            listing.source_url, "https://facebook.com/marketplace/item/1"
        )
        self.assertEqual(listing.seller_exact_location, "Exact City, MS")
        self.assertEqual(listing.raw_capture_text, "original marketplace text")
        self.assertEqual(listing.public_status, "verification_in_progress")
        self.assertEqual(listing.facebook_intake_status, "resolved")
        self.assertTrue(listing.comp_last_calculated_at)

        wizard.action_import()
        duplicates = self.Listing.search(
            [
                ("source", "=", "facebook_marketplace"),
                ("source_listing_id", "=", "FB-TEST-001"),
            ]
        )
        self.assertEqual(len(duplicates), 1)

    def test_broker_can_queue_and_deduplicate_facebook_share_link(self):
        share_url = "https://www.facebook.com/share/1BpdNk4kak/?tracking=test"
        wizard = self.env[
            "southern.equipment.facebook.intake.wizard"
        ].create({"facebook_url": share_url})

        action = wizard.action_queue_enrichment()
        listing = self.Listing.browse(action["res_id"])

        self.assertEqual(
            listing.source_url,
            "https://www.facebook.com/share/1BpdNk4kak",
        )
        self.assertEqual(listing.facebook_shared_url, listing.source_url)
        self.assertEqual(listing.facebook_intake_status, "pending")
        self.assertEqual(listing.public_status, "verification_in_progress")
        self.assertFalse(listing.website_published)
        self.assertFalse(listing.source_listing_id)
        self.assertEqual(listing.broker_id, self.env.user)

        duplicate_wizard = self.env[
            "southern.equipment.facebook.intake.wizard"
        ].create({"facebook_url": share_url})
        duplicate_action = duplicate_wizard.action_queue_enrichment()
        self.assertEqual(duplicate_action["res_id"], listing.id)
        self.assertEqual(
            self.Listing.search_count(
                [
                    ("source", "=", "facebook_marketplace"),
                    ("facebook_shared_url", "=", listing.source_url),
                ]
            ),
            1,
        )

    def test_direct_facebook_item_link_extracts_numeric_identity(self):
        wizard = self.env[
            "southern.equipment.facebook.intake.wizard"
        ].create(
            {
                "facebook_url": (
                    "https://www.facebook.com/marketplace/item/1488670613016588/"
                    "?rdid=tracking"
                )
            }
        )

        action = wizard.action_queue_enrichment()
        listing = self.Listing.browse(action["res_id"])

        self.assertEqual(listing.source_listing_id, "1488670613016588")
        self.assertEqual(
            listing.source_url,
            "https://www.facebook.com/marketplace/item/1488670613016588",
        )
        self.assertEqual(listing.facebook_intake_status, "pending")

    def test_facebook_intake_rejects_non_listing_url(self):
        wizard = self.env[
            "southern.equipment.facebook.intake.wizard"
        ].create({"facebook_url": "https://example.com/not-facebook"})
        with self.assertRaises(UserError):
            wizard.action_queue_enrichment()

    def test_terminal_status_automatically_unpublishes(self):
        listing = self.Listing.create(
            self._listing_values(website_published=True)
        )
        for status in ("unavailable", "sold", "archived", "assigned"):
            listing.write(
                {
                    "public_status": "published",
                    "website_published": True,
                }
            )
            listing.public_status = status
            self.assertFalse(listing.website_published)

    def test_source_url_canonicalization_removes_tracking_parameters(self):
        listing = self.Listing.create(
            self._listing_values(
                source="dealer",
                source_url=(
                    "HTTPS://Dealer.Example/listing/1/?utm_source=test"
                    "&b=2&gclid=tracking&a=1#photos"
                ),
            )
        )
        self.assertEqual(
            listing.source_url,
            "https://dealer.example/listing/1?a=1&b=2",
        )

    def test_public_vin_approval_requires_a_serial(self):
        with self.assertRaises(ValidationError):
            self.Listing.create(
                self._listing_values(show_vin_serial_publicly=True)
            )

    def test_website_inquiry_creates_partner_lead_and_activity(self):
        listing = self.Listing.create(
            self._listing_values(
                broker_id=self.env.user.id,
                website_published=True,
            )
        )
        inquiry = self.Inquiry.create_from_website(
            listing,
            {
                "contact_name": "Test Buyer",
                "phone": "555-0199",
                "email": "buyer@example.com",
                "company": "Buyer Company",
                "buyer_location": "Jackson, MS",
                "budget": 50000,
                "timeline": "30_days",
                "financing_needed": True,
                "trade_in": False,
                "message": "Please coordinate an inspection.",
                "website_submission": True,
                "privacy_consent_at": fields.Datetime.now(),
                "privacy_notice_version": "2026-07",
                "submission_fingerprint": "test-request-fingerprint",
            },
            broker=self.env.user,
        )
        self.assertTrue(inquiry.partner_id)
        self.assertTrue(inquiry.crm_lead_id)
        self.assertEqual(inquiry.crm_lead_id.type, "opportunity")
        self.assertTrue(inquiry.activity_ids)
        self.assertTrue(inquiry.website_submission)
        self.assertTrue(inquiry.privacy_consent_at)
        self.assertEqual(inquiry.privacy_notice_version, "2026-07")
        self.assertEqual(listing.public_status, "inquiry_received")
        duplicate = self.Inquiry.create_from_website(
            listing,
            {
                "contact_name": "Test Buyer",
                "phone": "555-0199",
                "email": "buyer@example.com",
                "company": "Buyer Company",
                "buyer_location": "Jackson, MS",
                "budget": 50000,
                "timeline": "30_days",
                "financing_needed": True,
                "trade_in": False,
                "message": "Duplicate browser submission.",
                "website_submission": True,
                "privacy_consent_at": fields.Datetime.now(),
                "privacy_notice_version": "2026-07",
                "submission_fingerprint": "test-request-fingerprint",
            },
            broker=self.env.user,
        )
        self.assertEqual(duplicate, inquiry)
        self.assertEqual(
            self.env["crm.lead"].search_count(
                [("name", "=", "Equipment Deal Request: 2022 Test Skid Steer")]
            ),
            1,
        )

    def test_website_inquiry_fingerprint_is_rate_limited(self):
        listing = self.Listing.create(self._listing_values())
        fingerprint = "rate-limit-test-fingerprint"
        self.Inquiry.create(
            [
                {
                    "listing_id": listing.id,
                    "contact_name": f"Rate Test {index}",
                    "phone": f"555-02{index:02d}",
                    "email": f"rate-{index}@example.com",
                    "submission_fingerprint": fingerprint,
                }
                for index in range(20)
            ]
        )
        inquiry = self.Inquiry.create_from_website(
            listing,
            {
                "contact_name": "Blocked Request",
                "phone": "555-0299",
                "email": "blocked-rate@example.com",
                "submission_fingerprint": fingerprint,
            },
        )
        self.assertFalse(inquiry)

    def test_deposit_inspection_and_assignment_guards(self):
        listing = self.Listing.create(self._listing_values())
        buyer = self.env["res.partner"].create({"name": "Test Buyer"})
        inquiry = self.Inquiry.create(
            {
                "listing_id": listing.id,
                "partner_id": buyer.id,
                "contact_name": buyer.name,
                "phone": "555-0123",
                "email": "deal-buyer@example.com",
            }
        )
        deal = self.env["southern.brokered.deal"].create(
            {
                "listing_id": listing.id,
                "buyer_inquiry_id": inquiry.id,
                "buyer_id": buyer.id,
                "seller_id": self.seller.id,
                "broker_id": self.env.user.id,
            }
        )
        with self.assertRaises(UserError):
            deal.action_close()
        with self.assertRaises(UserError):
            deal.action_request_deposit()
        deal.deposit_required = 1000
        deal.action_request_deposit()
        self.assertEqual(deal.stage, "deposit_pending")
        ledger = self.env["southern.deposit.ledger"].create(
            {
                "deal_id": deal.id,
                "amount": 1000,
                "transaction_type": "deposit",
            }
        )
        ledger.action_post()
        self.assertEqual(deal.stage, "deposit_received")
        self.assertEqual(deal.deposit_received, 1000)
        inspection_action = deal.action_create_inspection()
        inspection = self.env["southern.inspection.order"].browse(
            inspection_action["res_id"]
        )
        with self.assertRaises(UserError):
            inspection.action_mark_complete()
        inspection.summary = "<p>Inspection complete.</p>"
        with self.assertRaises(UserError):
            inspection.action_mark_complete()
        inspection.pass_fail = "pass"
        inspection.action_mark_complete()
        self.assertEqual(deal.stage, "inspection_complete")
        assignment_action = deal.action_create_assignment()
        assignment = self.env["southern.contract.assignment"].browse(
            assignment_action["res_id"]
        )
        with self.assertRaises(UserError):
            assignment.action_execute()
        assignment.write(
            {
                "purchase_contract_status": "executed",
                "purchase_contract_file": TEST_IMAGE,
                "buyer_approval_received": True,
                "southern_approval_received": True,
            }
        )
        with self.assertRaises(UserError):
            assignment.action_execute()
        assignment.assignment_agreement_file = TEST_IMAGE
        assignment.action_execute()
        self.assertEqual(deal.stage, "assigned")
        deal.action_close()
        self.assertEqual(deal.stage, "closed")
        self.assertEqual(listing.public_status, "sold")

        refund = self.env["southern.deposit.ledger"].create(
            {
                "deal_id": deal.id,
                "amount": 1500,
                "transaction_type": "refund",
            }
        )
        with self.assertRaises(UserError):
            refund.action_post()
        ledger.action_void()
        self.assertEqual(deal.deposit_received, 0)
        self.assertEqual(deal.deposit_status, "not_requested")

    def test_deal_requires_verified_seller_contact(self):
        listing = self.Listing.create(
            self._listing_values(source_seller_id=False)
        )
        inquiry = self.Inquiry.create(
            {
                "listing_id": listing.id,
                "contact_name": "Buyer",
                "phone": "555-0102",
                "email": "buyer-no-seller@example.com",
            }
        )
        with self.assertRaises(UserError):
            inquiry.action_create_deal()


@tagged("southern_brokerage", "post_install", "-at_install")
class TestSouthernEquipmentBrokerageWebsite(HttpCase):
    def test_privacy_notice_route_is_available(self):
        response = self.url_open("/privacy")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Southern Equipment handles information", response.content)
