from odoo import SUPERUSER_ID, _, api
from odoo.exceptions import ValidationError

from .models.mississippi_withholding import MS_FILING_STATUSES

SOUTHERN_COMPANY_NAME = "Southern Equipment Company (Laurel)"
US_REGULAR_PAY_STRUCTURE = "United States: Regular Pay"

SOUTHERN_PAYROLL_ACCOUNTS = {
    "accrued_payroll": {
        "code": "210010",
        "name": "Accrued Payroll",
        "account_type": "liability_current",
        "create": False,
    },
    "employee_taxes": {
        "code": "210011",
        "name": "Employee Payroll Taxes Payable",
        "account_type": "liability_current",
        "create": True,
    },
    "employer_taxes": {
        "code": "210012",
        "name": "Employer Payroll Taxes Payable",
        "account_type": "liability_current",
        "create": True,
    },
    "mississippi_withholding": {
        "code": "210013",
        "name": "Mississippi Withholding Payable",
        "account_type": "liability_current",
        "create": True,
    },
    "wage_expense": {
        "code": "600000",
        "name": "Administrative Payroll",
        "account_type": "expense",
        "create": False,
    },
    "employer_tax_expense": {
        "code": "600020",
        "name": "Employer Payroll Taxes",
        "account_type": "expense",
        "create": False,
    },
}

SOUTHERN_PAYROLL_RULE_MAPPINGS = {
    "GROSS": {"account_debit": "wage_expense"},
    "FIT": {"account_debit": "employee_taxes"},
    "SST": {"account_debit": "employee_taxes"},
    "MEDICARE": {"account_debit": "employee_taxes"},
    "MEDICAREADD": {"account_debit": "employee_taxes"},
    "MSINCOMETAX": {"account_debit": "mississippi_withholding"},
    "COMPANYSOCIAL": {
        "account_debit": "employer_tax_expense",
        "account_credit": "employer_taxes",
    },
    "COMPANYMEDICARE": {
        "account_debit": "employer_tax_expense",
        "account_credit": "employer_taxes",
    },
    "COMPANYFUTA": {
        "account_debit": "employer_tax_expense",
        "account_credit": "employer_taxes",
    },
    "COMPANYSUI": {
        "account_debit": "employer_tax_expense",
        "account_credit": "employer_taxes",
    },
    "NET": {"account_credit": "accrued_payroll"},
}


def _field_name(model, *names):
    for name in names:
        if name in model._fields:
            return name
    return None


def _require_one(records, description):
    if len(records) != 1:
        raise ValidationError(
            _("Expected exactly one %(description)s; found %(count)s.")
            % {"description": description, "count": len(records)}
        )
    return records


def _southern_companies(env):
    companies = env["res.company"].sudo().search([("name", "=", SOUTHERN_COMPANY_NAME)])
    if len(companies) > 1:
        raise ValidationError(_("Multiple companies are named %s.") % SOUTHERN_COMPANY_NAME)
    return companies


def _regular_pay_structure(env):
    return _require_one(
        env["hr.payroll.structure"].sudo().search([("name", "=", US_REGULAR_PAY_STRUCTURE)]),
        _("payroll structure named %s") % US_REGULAR_PAY_STRUCTURE,
    )


def _ensure_ms_rule(env, structure=None):
    structure = structure or _regular_pay_structure(env)
    category = _require_one(
        env["hr.salary.rule.category"].sudo().search([("name", "=", "Taxes")]),
        _("salary rule category named Taxes"),
    )
    rule_model = env["hr.salary.rule"].sudo()
    values = {
        "name": "MS State Income Tax",
        "code": "MSINCOMETAX",
        "sequence": 162,
        "struct_id": structure.id,
        "category_id": category.id,
        "condition_select": "python",
        "condition_python": f"result = employee.l10n_us_state_filing_status in {tuple(MS_FILING_STATUSES)!r}",
        "amount_select": "code",
        "amount_python_compute": (
            "taxable = categories['TAXABLE'] or 0.0\n"
            "result = -employee._l10n_us_ms_state_withholding(taxable, payslip)"
        ),
        "appears_on_payslip": True,
    }
    rules = rule_model.with_context(active_test=False).search(
        [("code", "=", "MSINCOMETAX"), ("struct_id", "=", structure.id)]
    )
    if len(rules) > 1:
        raise ValidationError(_("Multiple MSINCOMETAX rules exist on United States: Regular Pay."))
    if rules:
        rules.write({**values, "active": True})
        return rules
    return rule_model.create(values)


def _account_for_company(env, company, specification):
    account_model = env["account.account"].sudo().with_company(company).with_context(active_test=False)
    accounts = account_model.search(
        [("company_ids", "in", company.ids), ("code", "=", specification["code"])]
    )
    if not accounts:
        if not specification["create"]:
            raise ValidationError(
                _("Required Southern payroll account %(code)s %(name)s does not exist.")
                % {"code": specification["code"], "name": specification["name"]}
            )
        return account_model.create(
            {
                "code": specification["code"],
                "name": specification["name"],
                "account_type": specification["account_type"],
                "company_ids": [(6, 0, company.ids)],
            }
        )
    account = _require_one(accounts, _("Southern account code %s") % specification["code"])
    if not account.active:
        raise ValidationError(_("Southern payroll account %s is archived.") % account.code)
    if account.name != specification["name"] or account.account_type != specification["account_type"]:
        raise ValidationError(
            _(
                "Southern account %(code)s must be %(name)s (%(account_type)s), "
                "not %(actual_name)s (%(actual_type)s)."
            )
            % {
                "code": specification["code"],
                "name": specification["name"],
                "account_type": specification["account_type"],
                "actual_name": account.name,
                "actual_type": account.account_type,
            }
        )
    if account.company_ids != company:
        raise ValidationError(
            _("Southern payroll account %s must belong only to Southern Equipment.") % account.code
        )
    return account


def configure_southern_payroll_accounts(env, company=None, structure=None):
    company = company or _require_one(
        _southern_companies(env),
        _("company named %s") % SOUTHERN_COMPANY_NAME,
    )
    structure = structure or _regular_pay_structure(env)
    accounts = {
        key: _account_for_company(env, company, specification)
        for key, specification in SOUTHERN_PAYROLL_ACCOUNTS.items()
    }

    rule_model = env["hr.salary.rule"].sudo()
    debit_field = _field_name(rule_model, "account_debit", "account_debit_id")
    credit_field = _field_name(rule_model, "account_credit", "account_credit_id")
    if not debit_field or not credit_field:
        raise ValidationError(_("Payroll accounting fields are unavailable; install US Payroll Accounting first."))

    for code, mapping in SOUTHERN_PAYROLL_RULE_MAPPINGS.items():
        rule = _require_one(
            rule_model.search([("code", "=", code), ("struct_id", "=", structure.id)]),
            _("salary rule %s on United States: Regular Pay") % code,
        ).with_company(company)
        values = {}
        if "account_debit" in mapping:
            values[debit_field] = accounts[mapping["account_debit"]].id
        if "account_credit" in mapping:
            values[credit_field] = accounts[mapping["account_credit"]].id
        rule.write(values)
    return accounts


def apply_southern_mississippi_payroll_setup(env):
    structure = _regular_pay_structure(env)
    _ensure_ms_rule(env, structure=structure)
    company = _southern_companies(env)
    if not company:
        return {}
    return configure_southern_payroll_accounts(env, company=company, structure=structure)


def post_init_hook(env):
    apply_southern_mississippi_payroll_setup(env)


def migration_environment(cr):
    return api.Environment(cr, SUPERUSER_ID, {})
