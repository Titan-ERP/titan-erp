from contextlib import contextmanager
from unittest.mock import MagicMock, Mock, patch

from odoo.tests.common import TransactionCase


class TestAccountTaxcloudCommon(TransactionCase):

    @contextmanager
    def mock_taxcloud(self):
        return_get_all_taxes_values = {
            "response": {
                "ResponseType": "OK",
                "Messages": None,
                "CartID": "443",
                "CartItemsResponse": {
                    "CartItemResponse": [
                        {"CartItemIndex": 0, "TaxAmount": 6.41},
                        {"CartItemIndex": 1, "TaxAmount": 0.641},
                    ]
                },
            },
            "values": {0: 6.41, 1: 0.641},
        }

        # ── V3 API response — must match exactly what
        # _check_taxcloud_response expects:
        # - NO 'error_message' key
        # - NO 'errors' key
        # - NO 'status' >= 400
        # ──────────────────────────────────────────────
        return_get_all_taxes_values_v3 = {
            "values": {0: 6.41, 1: 0.641},
            "cart_id": "mock-cart-id-443",
        }

        return_get_tic_category_value = {
            "data": [
                {"Description": "Mock TIC Code 1", "TICID": "0001"},
                {"Description": "Mock TIC Code 2", "TICID": "0002"},
            ]
        }
        return_verify_address_value = {
            "Address1": "250 Executive Park Blvd",
            "Address2": "",
            "City": "San Francisco",
            "State": self.env.ref("base.state_us_5").id,
            "Zip4": "94134",
            "Zip5": "",
        }

        authorize_with_capture_mock_response = {
            "ResponseType": "Success",
            "Messages": ["Authorize and Captured in Taxcloud"],
        }

        returned_mock_response = {
            "ResponseType": "Success",
            "Messages": ["Returned from Taxcloud"],
        }

        captured_mock_response = {
            "ResponseType": "Success",
            "Messages": ["Captured in Taxcloud"],
        }

        try:
            base = "odoo.addons.account_taxcloud_tc.models.taxcloud_request.TaxCloudRequest"

            with patch(
                f"{base}.verify_address",
                new=Mock(return_value=return_verify_address_value),
            ), patch(
                f"{base}.get_tic_category",
                new=Mock(return_value=return_get_tic_category_value),
            ), patch(
                f"{base}.get_all_taxes_values",
                new=Mock(return_value=return_get_all_taxes_values),
            ), patch(
                # ── KEY FIX: patch v3 method directly ───────────────
                f"{base}._get_all_taxes_values_v3",
                new=Mock(return_value=return_get_all_taxes_values_v3),
            ), patch(
                f"{base}.get_taxcloud_authorize_with_capture",
                new=Mock(return_value=authorize_with_capture_mock_response),
            ), patch(
                f"{base}.get_taxcloud_returned",
                new=Mock(return_value=returned_mock_response),
            ), patch(
                f"{base}.get_taxcloud_captured",
                new=Mock(return_value=captured_mock_response),
            ):
                yield
        finally:
            pass

    @classmethod
    def setUpClass(cls):
        res = super().setUpClass()
        cls.TAXCLOUD_LOGIN_ID = "TAXCLOUD_LOGIN_ID"
        cls.TAXCLOUD_API_KEY = "TAXCLOUD_API_KEY"

        # Save Taxcloud credentials and sync TICs
        config = cls.env["res.config.settings"].create({
            "taxcloud_api_id_v3": cls.TAXCLOUD_LOGIN_ID,
            "taxcloud_api_key_v3": cls.TAXCLOUD_API_KEY,
        })
        with cls.mock_taxcloud(cls):
            config.sync_taxcloud_category()
            tic_computer = cls.env["product.tic.category"].search(
                [("code", "=", "0001")]
            )
            config.tic_category_id = tic_computer
            config.execute()

        # Fiscal position
        cls.fiscal_position = cls.env.ref(
            "account_taxcloud_tc.account_fiscal_position_taxcloud_us"
        )
        # Sale journal — search or create
        cls.journal = cls.env["account.journal"].search([
            ("type", "=", "sale"),
            ("company_id", "=", cls.env.company.id),
        ], limit=1)
        if not cls.journal:
            cls.journal = cls.env["account.journal"].create({
                "name": "Customer Invoices",
                "type": "sale",
                "code": "INV",
                "company_id": cls.env.company.id,
            })

        # Income account — search or create
        cls.income_account = cls.env["account.account"].search([
            ("account_type", "in", ["income", "income_other"]),
            ("company_ids", "in", cls.env.company.id),
        ], limit=1)
        if not cls.income_account:
            cls.income_account = cls.env["account.account"].create({
                "name": "Test Income Account",
                "code": "400001",
                "account_type": 'income',
                "company_ids": [(6, 0, [cls.env.company.id])],
            })

        # Update company address
        cls.env.company.write({
            "street": "250 Executive Park Blvd",
            "city": "San Francisco",
            "state_id": cls.env.ref("base.state_us_5").id,
            "country_id": cls.env.ref("base.us").id,
            "zip": "94134",
        })

        # ── Receivable account ──────────────────────────────────────
        cls.receivable_account = cls.env['account.account'].search([
            ('account_type', '=', 'asset_receivable'),
            ('company_ids', 'in', cls.env.company.id),
        ], limit=1)

        if not cls.receivable_account:
            cls.receivable_account = cls.env['account.account'].create({
                'name': 'Test Receivable Account',
                'code': '130001',
                'account_type': 'asset_receivable',
                "company_ids": [(6, 0, [cls.env.company.id])],
                'reconcile': True,
            })

        # ── Payable account ─────────────────────────────────────────
        cls.payable_account = cls.env['account.account'].search([
            ('account_type', '=', 'liability_payable'),
            ('company_ids', 'in', cls.env.company.id),
        ], limit=1)

        if not cls.payable_account:
            cls.payable_account = cls.env['account.account'].create({
                'name': 'Test Payable Account',
                'code': '200001',
                'account_type': 'liability_payable',
                "company_ids": [(6, 0, [cls.env.company.id])],
                'reconcile': True,
            })

        # ── Set accounts on partner ─────────────────────────────────
        cls.partner = cls.env['res.partner'].create({
            'name': 'Sale Partner',
            'street': '77 Santa Barbara Rd',
            'city': 'Pleasant Hill',
            'state_id': cls.env.ref('base.state_us_5').id,
            'country_id': cls.env.ref('base.us').id,
            'zip': '94523',
            'property_account_receivable_id': cls.receivable_account.id,
            'property_account_payable_id': cls.payable_account.id,
            # 'tax_number_type': 'TaxID',
        })

        # Create products with income account and no default taxes
        cls.product = cls.env["product.product"].create({
            "name": "Test Product",
            "list_price": 1000.00,
            "standard_price": 200.00,
            "taxes_id": [(5, 0, 0)],          # clear customer taxes
            "supplier_taxes_id": [(5, 0, 0)], # clear vendor taxes
            "property_account_income_id": cls.income_account.id,
        })
        cls.product_1 = cls.env["product.product"].create({
            "name": "Test 1 Product",
            "list_price": 100.00,
            "standard_price": 50.00,
            "taxes_id": [(5, 0, 0)],          # clear customer taxes
            "supplier_taxes_id": [(5, 0, 0)], # clear vendor taxes
            "property_account_income_id": cls.income_account.id,
        })
        bank_journal = cls.env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', cls.env.company.id)], limit=1)
        # ==== Payment methods ====
        def get_fallback_outstanding_account(name, code):
            return cls.env.company.transfer_account_id or cls.env['account.account'].search([
                ('code', '=', code),
                ('company_ids', 'in', cls.env.company.id),
            ], limit=1) or cls.env['account.account'].create({
                'name': name,
                'code': code,
                'account_type': 'asset_current',
                'company_ids': [(6, 0, [cls.env.company.id])],
                'reconcile': True,
            })

        in_outstanding_account = cls.env['account.chart.template'].ref('account_journal_payment_debit_account_id', raise_if_not_found=False)
        out_outstanding_account = cls.env['account.chart.template'].ref('account_journal_payment_credit_account_id', raise_if_not_found=False)
        if not in_outstanding_account:
            in_outstanding_account = get_fallback_outstanding_account('Test Outstanding Receipts', '101401')
        if not out_outstanding_account:
            out_outstanding_account = get_fallback_outstanding_account('Test Outstanding Payments', '101402')
        if not bank_journal:
            bank_journal = cls.env['account.journal'].create({
                'name': 'Test Bank Journal',
                'type': 'bank',
                'company_id': cls.env.company.id,
            })
        if bank_journal:
            cls.inbound_payment_method_line = bank_journal.inbound_payment_method_line_ids[0]
            cls.inbound_payment_method_line.payment_account_id = in_outstanding_account
            cls.outbound_payment_method_line = bank_journal.outbound_payment_method_line_ids[0]
            cls.outbound_payment_method_line.payment_account_id = out_outstanding_account

        return res
