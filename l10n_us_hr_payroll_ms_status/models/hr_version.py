from odoo import api, models

from .mississippi_withholding import with_ms_filing_statuses


class HrVersion(models.Model):
    _inherit = "hr.version"

    def _get_selection_state_filing_status(self):
        return with_ms_filing_statuses(super()._get_selection_state_filing_status())

    @api.model
    def fields_get(self, allfields=None, attributes=None):
        result = super().fields_get(allfields=allfields, attributes=attributes)
        field = result.get("l10n_us_state_filing_status")
        if field and "selection" in field:
            field["selection"] = with_ms_filing_statuses(field["selection"])
        return result
