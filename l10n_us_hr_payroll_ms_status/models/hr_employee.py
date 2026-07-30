from decimal import Decimal

from odoo import fields, models

from .mississippi_withholding import MS_FILING_STATUSES, MS_PAY_PERIODS, calculate_ms_withholding


STATE_FILING_SELECTION = [
    ("ca_status_1", "CA: Single, Dual Income Married or Married with Multiple Employers"),
    ("ca_status_2", "CA: Married: One Income"),
    ("ca_status_4", "CA: Unmarried Head of Household"),
    ("ny_status_1", "NY: Single or Head of Household"),
    ("ny_status_2", "NY: Married (filing jointly)"),
    ("ny_status_3", "NY: Married, but withhold at a higher single rate"),
    ("al_status_1", "AL: 0: No Exemption Made (withhold at the highest rate)"),
    ("al_status_2", "AL: S: Single"),
    ("al_status_3", "AL: MS: Married filing Separately"),
    ("al_status_4", "AL: M: Married"),
    ("al_status_5", "AL: H: Head of Household"),
    ("co_status_1", "CO: Single or Married filing Separately"),
    ("co_status_2", "CO: Married filing Jointly or Qualifying Surviving Spouse"),
    ("co_status_3", "CO: Head of Household"),
    ("vt_status_1", "VT: Single"),
    ("vt_status_2", "VT: Married/Civil Union Filing Jointly"),
    ("vt_status_3", "VT: Married/Civil Union Filing Separately"),
    ("vt_status_4", "VT: Married, but withhold at a higher single rate"),
    ("il_status_1", "IL: General rate used for deductions"),
    ("az_status_1", "AZ: Withhold wages at 0.5%"),
    ("az_status_2", "AZ: Withhold wages at 1.0%"),
    ("az_status_3", "AZ: Withhold wages at 1.5%"),
    ("az_status_4", "AZ: Withhold wages at 2.0%"),
    ("az_status_5", "AZ: Withhold wages at 2.5%"),
    ("az_status_6", "AZ: Withhold wages at 3.0%"),
    ("az_status_7", "AZ: Withhold wages at 3.5%"),
    ("dc_status_1", "DC: Single"),
    ("dc_status_2", "DC: Married/domestic partners filing jointly/qualifying widow(er) with dependent child"),
    ("dc_status_3", "DC: Head of household"),
    ("dc_status_4", "DC: Married filing separately"),
    ("dc_status_5", "DC: Married/domestic partners filing separately on same return"),
    ("nc_status_1", "NC: Single or Married Filing Separately"),
    ("nc_status_2", "NC: Head of Household"),
    ("nc_status_3", "NC: Married Filing Jointly or Surviving Spouse"),
    ("va_status_1", "VA: Single"),
    ("va_status_2", "VA: Married, Filing a Joint Return"),
    ("va_status_3", "VA: Married, Filing a Separate Return"),
    ("or_status_1", "OR: Single"),
    ("or_status_2", "OR: Married"),
    ("id_status_1", "ID: Single"),
    ("id_status_2", "ID: Married"),
    ("id_status_3", "ID: Married, but withhold at Single rate"),
    *[(key, config["label"]) for key, config in MS_FILING_STATUSES.items()],
]


class HrEmployee(models.Model):
    _inherit = "hr.employee"

    l10n_us_state_filing_status = fields.Selection(
        selection=STATE_FILING_SELECTION,
        string="State Tax Filing Status",
    )

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
