from collections import Counter

from odoo import _, api, fields, models


class CrmLead(models.Model):
    _inherit = "crm.lead"

    southern_record_class = fields.Selection(
        [
            ("actual_opportunity", "Actual Opportunity"),
            ("imported_reference", "Imported Reference"),
            ("disqualified_reference", "Disqualified Reference"),
        ],
        string="Southern Record Class",
        default="actual_opportunity",
        required=True,
        tracking=True,
        index=True,
        help="Separates worked opportunities from imported research/reference records.",
    )
    southern_classification_reason = fields.Char(readonly=True)
    southern_classified_at = fields.Datetime(readonly=True)

    @api.model
    def action_classify_reference_cohorts(self, mass_import_threshold=50):
        leads = self.with_context(active_test=False).search([])
        cohort_counts = Counter(
            fields.Datetime.to_datetime(lead.create_date).date() for lead in leads if lead.create_date
        )
        changed = 0
        for lead in leads:
            create_day = (
                fields.Datetime.to_datetime(lead.create_date).date() if lead.create_date else False
            )
            commercial_signals = [
                bool(lead.partner_id),
                bool(lead.email_from),
                bool(lead.description),
                bool(lead.activity_ids),
                (lead.expected_revenue or 0.0) > 0,
                (lead.probability or 0.0) >= 100,
                bool(lead.stage_id and lead.stage_id.sequence > 1),
                bool(
                    lead.user_id
                    and (lead.user_id.name or "").casefold() != "administrator"
                ),
            ]
            imported = (
                bool(create_day)
                and cohort_counts[create_day] >= mass_import_threshold
                and not any(commercial_signals)
            )
            target = "imported_reference" if imported else "actual_opportunity"
            if lead.southern_record_class != target:
                lead.write(
                    {
                        "southern_record_class": target,
                        "southern_classification_reason": (
                            _("Large same-day import cohort without commercial activity")
                            if imported
                            else _("Commercial activity or individually-created opportunity")
                        ),
                        "southern_classified_at": fields.Datetime.now(),
                    }
                )
                changed += 1
        return changed
