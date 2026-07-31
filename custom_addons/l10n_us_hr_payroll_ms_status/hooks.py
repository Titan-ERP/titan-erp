from .models.mississippi_withholding import MS_FILING_STATUSES


def _field_name(model, *names):
    for name in names:
        if name in model._fields:
            return name
    return None


def post_init_hook(env):
    structures = env["hr.payroll.structure"].search([("name", "=", "United States: Regular Pay")], limit=1)
    category = env["hr.salary.rule.category"].search([("name", "=", "Taxes")], limit=1)
    if not structures or not category:
        return

    account = env["account.account"].search([("code", "=", "230100")], limit=1)
    rule_model = env["hr.salary.rule"]
    debit_field = _field_name(rule_model, "account_debit_id", "account_debit")

    values = {
        "name": "MS State Income Tax",
        "code": "MSINCOMETAX",
        "sequence": 162,
        "struct_id": structures.id,
        "category_id": category.id,
        "condition_select": "python",
        "condition_python": "result = employee.l10n_us_state_filing_status in %r" % (tuple(MS_FILING_STATUSES),),
        "amount_select": "code",
        "amount_python_compute": (
            "taxable = getattr(categories, 'TAXABLE', 0.0) or 0.0\n"
            "result = -employee._l10n_us_ms_state_withholding(taxable, payslip)"
        ),
        "appears_on_payslip": True,
    }
    if debit_field and account:
        values[debit_field] = account.id

    existing = rule_model.search([("code", "=", "MSINCOMETAX"), ("struct_id", "=", structures.id)], limit=1)
    if existing:
        existing.write(values)
    else:
        rule_model.create(values)
