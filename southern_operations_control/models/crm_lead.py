from datetime import timedelta

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
    southern_source_system = fields.Char(
        index=True,
        help="Explicit originating system, integration, or import process.",
    )
    southern_import_batch_key = fields.Char(
        index=True,
        copy=False,
        help="Stable external import batch key used for provenance and rollback review.",
    )
    southern_imported_at = fields.Datetime(readonly=True, copy=False)
    southern_classification_version = fields.Char(readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        source_system = self.env.context.get("southern_source_system")
        import_batch_key = self.env.context.get("southern_import_batch_key")
        for vals in vals_list:
            if source_system and not vals.get("southern_source_system"):
                vals["southern_source_system"] = source_system
            if import_batch_key and not vals.get("southern_import_batch_key"):
                vals["southern_import_batch_key"] = import_batch_key
            if vals.get("southern_import_batch_key") and not vals.get("southern_imported_at"):
                vals["southern_imported_at"] = fields.Datetime.now()
        return super().create(vals_list)

    @api.model
    def action_classify_reference_cohorts(
        self,
        mass_import_threshold=50,
        limit=500,
        apply=False,
        company_id=None,
    ):
        limit = max(1, min(int(limit or 500), 1000))
        company_id = int(company_id or self.env.company.id)
        base_domain = [
            ("company_id", "=", company_id),
            ("southern_classified_at", "=", False),
        ]
        leads = self.with_context(active_test=False).search(
            base_domain,
            order="id",
            limit=limit,
        )
        cohort_counts = {}
        for lead in leads:
            if not lead.create_date:
                continue
            create_day = fields.Datetime.to_datetime(lead.create_date).date()
            if create_day in cohort_counts:
                continue
            day_start = fields.Datetime.to_string(fields.Datetime.to_datetime(str(create_day)))
            day_end = fields.Datetime.to_string(
                fields.Datetime.to_datetime(str(create_day)) + timedelta(days=1)
            )
            cohort_counts[create_day] = self.with_context(active_test=False).search_count(
                [
                    ("company_id", "=", company_id),
                    ("create_date", ">=", day_start),
                    ("create_date", "<", day_end),
                ]
            )
        changed = 0
        preview = {"actual_opportunity": 0, "imported_reference": 0}
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
            preview[target] += 1
            if lead.southern_record_class != target:
                changed += 1
            if apply:
                lead.write(
                    {
                        "southern_record_class": target,
                        "southern_classification_reason": (
                            _("Legacy same-day cohort without commercial activity")
                            if imported
                            else _("Commercial activity or individually-created opportunity")
                        ),
                        "southern_classified_at": fields.Datetime.now(),
                        "southern_classification_version": "2.0",
                    }
                )
        return {
            "apply": bool(apply),
            "company_id": company_id,
            "examined": len(leads),
            "would_change": changed,
            "classification": preview,
        }
