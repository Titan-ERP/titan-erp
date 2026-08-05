from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("at_install", "-post_install")
class TestSparexSourcing(TransactionCase):
    def test_price_pending_attempt_preserves_verified_url_evidence(self):
        product = self.env["product.template"].create(
            {"name": "Sparex test filter", "default_code": "S.165554", "purchase_ok": True}
        )
        supplier = self.env["res.partner"].create(
            {"name": "Sparex test supplier", "supplier_rank": 1}
        )

        row_id = self.env["southern.sparex.sourcing.queue"].record_external_attempt(
            product.id,
            {
                "supplier_id": supplier.id,
                "failure_code": "price_pending",
                "failure_reason": "Exact page loaded without a dealer price.",
                "evidence_url": "https://us.sparex.com/oil-filter-spin-on-165554.html",
                "evidence_sha256": "a" * 64,
                "evidence_schema_version": "1.0",
                "parser_version": "browser-exact-price-v3",
            },
        )

        row = self.env["southern.sparex.sourcing.queue"].browse(row_id)
        self.assertEqual(row.state, "cooldown")
        self.assertEqual(row.failure_code, "price_pending")
        self.assertEqual(
            row.evidence_url,
            "https://us.sparex.com/oil-filter-spin-on-165554.html",
        )
        self.assertEqual(row.evidence_sha256, "a" * 64)
        self.assertFalse(row.supplier_price)
