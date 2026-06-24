from .common import TestAccountTaxcloudCommon


class TestAccountTaxcloud(TestAccountTaxcloudCommon):

    def test_01_taxcloud_tax_rate_on_invoice(self):
        """Test TaxRate returned from TaxCloud is assigned on invoice lines."""

        invoice = self.env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner.id,
            "journal_id": self.journal.id,
            "fiscal_position_id": self.fiscal_position.id,
            "invoice_line_ids": [
                (0, 0, {
                    "product_id": self.product.id,
                    "account_id": self.income_account.id,
                    "tax_ids": [(5, 0, 0)],  # explicitly no taxes before posting
                    "price_unit": self.product.list_price,
                }),
                (0, 0, {
                    "product_id": self.product_1.id,
                    "account_id": self.income_account.id,
                    "tax_ids": [(5, 0, 0)],  # explicitly no taxes before posting
                    "price_unit": self.product_1.list_price,
                }),
            ],
        })

        # Verify no taxes before posting
        for line in invoice.invoice_line_ids:
            self.assertEqual(
                len(line.tax_ids),
                0,
                "There should be no tax on the line before posting.",
            )

        # Post with mocked TaxCloud — should assign taxes
        with self.mock_taxcloud():
            invoice.action_post()

        # Verify TaxCloud assigned exactly one tax per line
        for line in invoice.invoice_line_ids:
            self.assertEqual(
                len(line.tax_ids),
                1,
                "TaxCloud should have generated a unique tax rate for the line.",
            )