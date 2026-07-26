import base64
import csv
import io

from odoo import Command
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import TransactionCase, tagged


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
        }
        values.update(overrides)
        return values

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

        wizard.action_import()
        duplicates = self.Listing.search(
            [
                ("source", "=", "facebook_marketplace"),
                ("source_listing_id", "=", "FB-TEST-001"),
            ]
        )
        self.assertEqual(len(duplicates), 1)

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
            },
            broker=self.env.user,
        )
        self.assertTrue(inquiry.partner_id)
        self.assertTrue(inquiry.crm_lead_id)
        self.assertEqual(inquiry.crm_lead_id.type, "opportunity")
        self.assertTrue(inquiry.activity_ids)
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
