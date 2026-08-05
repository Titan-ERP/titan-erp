from unittest import SkipTest

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from ..hooks import (
    SOUTHERN_COMPANY_NAME,
    SOUTHERN_FUTA_EFFECTIVE_RATE,
    SOUTHERN_PAYROLL_ACCOUNTS,
    SOUTHERN_PAYROLL_RULE_MAPPINGS,
    US_REGULAR_PAY_STRUCTURE,
    configure_southern_employer_tax_rules,
    configure_southern_payroll_accounts,
)


@tagged("post_install", "-at_install")
class TestSouthernPayrollAccountMapping(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].search([("name", "=", SOUTHERN_COMPANY_NAME)])
        if not cls.company:
            raise SkipTest("Southern Equipment test company is not present.")
        if len(cls.company) != 1:
            raise AssertionError("Southern Equipment company name is ambiguous.")
        cls.structure = cls.env["hr.payroll.structure"].search([("name", "=", US_REGULAR_PAY_STRUCTURE)])
        if len(cls.structure) != 1:
            raise AssertionError("United States: Regular Pay structure is missing or ambiguous.")

    def test_mapping_is_company_scoped_and_idempotent(self):
        other_company = self.env["res.company"].search([("id", "!=", self.company.id)], limit=1)
        rules = self.env["hr.salary.rule"].search(
            [
                ("struct_id", "=", self.structure.id),
                ("code", "in", list(SOUTHERN_PAYROLL_RULE_MAPPINGS)),
            ]
        )
        other_before = {}
        if other_company:
            other_before = {
                rule.code: (
                    rule.with_company(other_company).account_debit.id,
                    rule.with_company(other_company).account_credit.id,
                )
                for rule in rules
            }

        first = configure_southern_payroll_accounts(
            self.env,
            company=self.company,
            structure=self.structure,
        )
        second = configure_southern_payroll_accounts(
            self.env,
            company=self.company,
            structure=self.structure,
        )

        self.assertEqual(
            {key: account.id for key, account in first.items()},
            {key: account.id for key, account in second.items()},
        )
        for key, specification in SOUTHERN_PAYROLL_ACCOUNTS.items():
            account = first[key]
            self.assertEqual(account.code, specification["code"])
            self.assertEqual(account.name, specification["name"])
            self.assertEqual(account.account_type, specification["account_type"])
            self.assertEqual(account.company_ids, self.company)

        for code, mapping in SOUTHERN_PAYROLL_RULE_MAPPINGS.items():
            rule = rules.filtered(lambda candidate, code=code: candidate.code == code).with_company(self.company)
            self.assertEqual(len(rule), 1)
            if "account_debit" in mapping:
                self.assertEqual(rule.account_debit, first[mapping["account_debit"]])
            if "account_credit" in mapping:
                self.assertEqual(rule.account_credit, first[mapping["account_credit"]])

        if other_company:
            other_after = {
                rule.code: (
                    rule.with_company(other_company).account_debit.id,
                    rule.with_company(other_company).account_credit.id,
                )
                for rule in rules
            }
            self.assertEqual(other_after, other_before)

    def test_net_salary_never_uses_floorplan_payable(self):
        accounts = configure_southern_payroll_accounts(
            self.env,
            company=self.company,
            structure=self.structure,
        )
        net_rule = self.env["hr.salary.rule"].search(
            [("struct_id", "=", self.structure.id), ("code", "=", "NET")]
        ).with_company(self.company)
        self.assertEqual(net_rule.account_credit, accounts["accrued_payroll"])
        self.assertNotEqual(net_rule.account_credit.name, "Equipment Floorplan Payable")

    def test_mississippi_rule_uses_safe_eval_supported_category_access(self):
        rule = self.env["hr.salary.rule"].search(
            [("struct_id", "=", self.structure.id), ("code", "=", "MSINCOMETAX")]
        )
        self.assertEqual(len(rule), 1)
        self.assertNotIn("getattr", rule.amount_python_compute)
        self.assertNotIn("categories.TAXABLE", rule.amount_python_compute)
        self.assertIn("categories['TAXABLE']", rule.amount_python_compute)

    def test_southern_employer_tax_rules_are_idempotent(self):
        first = configure_southern_employer_tax_rules(self.env, structure=self.structure)
        second = configure_southern_employer_tax_rules(self.env, structure=self.structure)

        self.assertEqual(first["futa"], second["futa"])
        self.assertEqual(first["sui"], second["sui"])
        self.assertIn(
            f"result_rate = {SOUTHERN_FUTA_EFFECTIVE_RATE}",
            first["futa"].amount_python_compute,
        )
        self.assertIn(SOUTHERN_COMPANY_NAME, first["futa"].amount_python_compute)
        self.assertEqual(
            first["sui"].condition_python,
            "result = version.private_state_id.code or version.address_id.state_id.code",
        )
        self.assertIn(
            "state_code = (version.private_state_id.code or "
            "version.address_id.state_id.code).lower()",
            first["sui"].amount_python_compute,
        )
