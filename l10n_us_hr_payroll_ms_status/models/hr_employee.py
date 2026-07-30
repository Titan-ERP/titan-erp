from decimal import Decimal

from odoo import fields, models

from .mississippi_withholding import MS_FILING_STATUSES, MS_PAY_PERIODS, calculate_ms_withholding


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    def _l10n_us_ms_state_withholding(self, taxable_wages, payslip=None):
        self.ensure_one()
        schedule_pay = self.schedule_pay or ""
        if payslip and getattr(payslip, "payslip_run_id", False) and payslip.payslip_run_id.schedule_pay:
            schedule_pay = payslip.payslip_run_id.schedule_pay
        pay_periods = MS_PAY_PERIODS.get(schedule_pay, Decimal("12"))
        return float(
            calculate_ms_withholding(
                taxable_wages,
                self.l10n_us_state_filing_status,
                pay_periods,
                self.l10n_us_state_withholding_allowance,
                self.l10n_us_state_extra_withholding,
            )
        )
