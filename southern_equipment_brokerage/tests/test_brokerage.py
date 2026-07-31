import base64
import csv
import io

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("southern_brokerage", "post_install", "-at_install")
class TestSouthernEquipmentBrokerage(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Listing = cls.env["southern.equipment.listing"]
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
            "public_price": 52500,
            "public_description": "<p>Public-safe listing description.</p>",
            "image_1920": base64.b64encode(b"representative-image"),
            "photo_rights_confirmed": True,
            "photo_source_note": "Generated representative image authorized for website use.",
            "image_is_representative": True,
        }
        values.update(overrides)
        return values

    def test_batch_slugs_are_unique_and_publish_requires_region(self):
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
                self._listing_values(public_price=0, website_published=True)
            )
        with self.assertRaises(ValidationError):
            self.Listing.create(
                self._listing_values(photo_rights_confirmed=False, website_published=True)
            )

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

    def test_facebook_agent_import_is_private_and_unpublished(self):
        headers = [
            "Opportunity", "Customer", "Contact Name", "Phone", "Email", "Stage",
            "Expected Revenue", "Priority", "Source", "Equipment ID",
            "Equipment Name", "Equipment Type", "Manufacturer", "Model", "Year",
            "VIN/Serial", "Ask Price", "Max Offer", "Projected Profit", "Margin %",
            "Location", "Facebook URL", "Internal Notes",
        ]
        row = [
            "Test opportunity", "Marketplace Seller", "Seller Name", "555-0111",
            "seller@example.com", "Qualified", "62000", "3",
            "Facebook Marketplace", "FB-TEST-001", "2021 Test Excavator",
            "Excavator", "Test Make", "EX100", "2021", "", "42000", "39000",
            "20000", "32", "Exact City, MS",
            "https://facebook.example/item/1",
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
            }
        )
        wizard.action_import()
        listing = self.Listing.search(
            [("source_listing_id", "=", "FB-TEST-001")], limit=1
        )
        self.assertTrue(listing)
        self.assertFalse(listing.website_published)
        self.assertFalse(listing.public_region)
        self.assertFalse(listing.vin_serial)
        self.assertEqual(listing.seller_exact_location, "Exact City, MS")
        self.assertEqual(listing.seller_ask_price, 42000)
        self.assertEqual(listing.ask_price, 42000)
        self.assertEqual(listing.public_price, 44100)
        self.assertEqual(listing.raw_capture_text, "original marketplace text")
        self.assertEqual(listing.public_status, "verification_in_progress")

    def test_canonical_facebook_enrichment_updates_existing_listing(self):
        listing = self.Listing.create(
            self._listing_values(
                source="facebook_marketplace",
                source_listing_id="1362964311864158",
                source_url="https://www.facebook.com/marketplace/item/1362964311864158",
                website_published=False,
            )
        )
        headers = [
            "Source", "Source Listing ID", "Public Title", "Source URL",
            "Capture Run ID", "Raw Capture Text", "Seller Name Raw",
            "Seller Phone", "Seller Exact Location", "Public Status",
            "Published on Website", "Equipment Type", "Manufacturer", "Model",
            "Year", "Hours", "VIN / Serial", "Ask Price", "Public Region",
            "Public Description", "Photo Rights Confirmed",
            "Photo Source / License Note", "Representative / Generic Image",
            "Internal Notes",
        ]
        row = [
            "Facebook Marketplace", "1362964311864158", "2022 Bobcat T870",
            "https://www.facebook.com/marketplace/item/1362964311864158",
            "RUN-20260725-001", "authorized source capture", "Isaac Williams",
            "555-0198", "Meridian, MS", "verification_in_progress", "False",
            "Skid Steer", "Bobcat", "T870", "2022", "3554", "B47C19759",
            "79000", "Mississippi",
            "<p>Public-safe opportunity description.</p>", "True",
            "Generated representative image authorized for website use.", "True",
            "Source facts retained for verification.",
        ]
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(headers)
        writer.writerow(row)
        wizard = self.env["southern.equipment.import.wizard"].create(
            {
                "upload_file": base64.b64encode(stream.getvalue().encode()),
                "upload_filename": "facebook-enrichment-update.csv",
                "update_existing": True,
            }
        )
        wizard.action_import()
        self.assertEqual(listing.public_title, "2022 Bobcat T870")
        self.assertEqual(listing.seller_ask_price, 79000)
        self.assertEqual(listing.public_price, 82950)
        self.assertEqual(listing.public_region, "Mississippi")
        self.assertTrue(listing.photo_rights_confirmed)
        self.assertTrue(listing.image_is_representative)
        self.assertEqual(listing.seller_exact_location, "Meridian, MS")
        self.assertEqual(listing.hours, 3554)
        self.assertEqual(listing.vin_serial, "B47C19759")
        self.assertEqual(listing.capture_run_id, "RUN-20260725-001")
        self.assertEqual(listing.raw_capture_text, "authorized source capture")
        self.assertFalse(listing.website_published)
        self.assertEqual(listing.public_status, "verification_in_progress")

    def test_facebook_source_link_must_match_before_publication(self):
        listing = self.Listing.create(
            self._listing_values(
                source="facebook_marketplace",
                source_listing_id="1362964311864158",
                source_url="https://www.facebook.com/marketplace/item/9999999999999999",
            )
        )
        self.assertFalse(listing.source_link_valid)
        with self.assertRaises(ValidationError):
            listing.action_publish()

        listing.write(
            {
                "source_url": (
                    "https://www.facebook.com/marketplace/item/1362964311864158"
                )
            }
        )
        self.assertTrue(listing.source_link_valid)
        listing.action_publish()
        self.assertTrue(listing.website_published)

    def test_website_inquiry_creates_partner_lead_and_activity(self):
        listing = self.Listing.create(
            self._listing_values(broker_id=self.env.user.id)
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
            },
            broker=self.env.user,
        )
        self.assertTrue(inquiry.partner_id)
        self.assertTrue(inquiry.crm_lead_id)
        self.assertEqual(inquiry.crm_lead_id.type, "opportunity")
        self.assertTrue(inquiry.activity_ids)
        self.assertEqual(listing.public_status, "needs_verification")

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
        inspection.action_mark_complete()
        self.assertEqual(deal.stage, "inspection_complete")
        assignment_action = deal.action_create_assignment()
        assignment = self.env["southern.contract.assignment"].browse(
            assignment_action["res_id"]
        )
        with self.assertRaises(UserError):
            assignment.action_execute()
