from odoo.addons.l10n_us_hr_payroll_ms_status.hooks import (
    apply_southern_mississippi_payroll_setup,
    migration_environment,
)


def migrate(cr, version):
    del version
    apply_southern_mississippi_payroll_setup(migration_environment(cr))
