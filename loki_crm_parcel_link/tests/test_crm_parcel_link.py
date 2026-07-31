from psycopg2 import IntegrityError

from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("at_install", "-post_install")
class TestLokiCrmParcelLink(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create({"name": "Parcel Test Shop"})
        cls.lead = cls.env["crm.lead"].create(
            {
                "name": "Parcel Test Opportunity",
                "partner_id": cls.partner.id,
                "company_id": cls.env.company.id,
            }
        )

    def _create_link(self, **values):
        values.setdefault("company_id", self.env.company.id)
        values.setdefault("crm_lead_id", self.lead.id)
        values.setdefault("partner_id", self.partner.id)
        values.setdefault("source_key", "dallas_cad")
        values.setdefault("parcel_account", "60084501460130000")
        values.setdefault("review_state", "matched")
        values.setdefault("confidence", 1.0)
        return self.env["loki.crm.parcel.link"].create(values)

    def test_match_is_exposed_on_crm_lead(self):
        link = self._create_link()
        self.assertEqual(self.lead.parcel_link_ids, link)
        self.assertEqual(self.lead.primary_parcel_link_id, link)
        self.assertEqual(self.lead.parcel_match_count, 1)

        action = self.lead.action_open_parcel_links()
        self.assertEqual(action["domain"], [("crm_lead_id", "=", self.lead.id)])
        self.assertEqual(action["context"]["default_partner_id"], self.partner.id)

    def test_unique_source_account_per_lead(self):
        self._create_link()
        with self.assertRaises(IntegrityError), self.env.cr.savepoint():
            self._create_link()

    def test_confidence_must_be_normalized(self):
        with self.assertRaises((IntegrityError, ValidationError)), self.env.cr.savepoint():
            self._create_link(confidence=1.1)

    def test_primary_link_prefers_highest_confidence_match(self):
        low = self._create_link(confidence=0.7)
        high = self._create_link(
            source_key="tarrant_cad",
            parcel_account="00000001",
            confidence=0.95,
        )
        self.assertEqual(self.lead.primary_parcel_link_id, high)
        self.assertEqual(self.lead.parcel_match_count, 2)

        self.lead.primary_parcel_link_id = low
        self.assertEqual(self.lead.primary_parcel_link_id, low)

    def test_only_matched_links_are_counted(self):
        self._create_link(review_state="review")
        self.assertEqual(self.lead.parcel_match_count, 0)
