from odoo import models

from .mississippi_withholding import MS_FILING_STATUSES


class HrVersion(models.Model):
    _inherit = "hr.version"

    def _get_selection_state_filing_status(self):
        selection = list(super()._get_selection_state_filing_status())
        existing = {value for value, _label in selection}
        for value, config in MS_FILING_STATUSES.items():
            if value not in existing:
                selection.append((value, config["label"]))
        return selection
