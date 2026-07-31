from odoo import api, fields, models


class LokiCrmParcelLink(models.Model):
    _name = "loki.crm.parcel.link"
    _description = "LOKI CRM Parcel Link"
    _rec_name = "parcel_account"
    _order = "matched_at desc, id desc"
    _check_company_auto = True

    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    crm_lead_id = fields.Many2one(
        "crm.lead",
        string="CRM Lead",
        required=True,
        ondelete="cascade",
        check_company=True,
        index=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Contact",
        ondelete="set null",
        check_company=True,
        index=True,
    )
    source_key = fields.Char(
        required=True,
        index=True,
        help="Stable key for the parcel source, such as dallas_cad.",
    )
    parcel_account = fields.Char(required=True, index=True)
    parcel_external_id = fields.Char(index=True)
    county = fields.Char(index=True)
    state_code = fields.Char(string="State", size=2, index=True)
    match_method = fields.Selection(
        [
            ("point_in_polygon", "Point in Polygon"),
            ("exact_account", "Exact Account"),
            ("address", "Address"),
            ("manual", "Manual"),
        ],
        required=True,
        default="point_in_polygon",
        index=True,
    )
    confidence = fields.Float(
        digits=(5, 4),
        help="Normalized match confidence from 0.0 through 1.0.",
    )
    review_state = fields.Selection(
        [
            ("matched", "Matched"),
            ("review", "Needs Review"),
            ("unmatched", "Unmatched"),
            ("rejected", "Rejected"),
        ],
        required=True,
        default="review",
        index=True,
    )
    latitude = fields.Float(digits=(10, 7))
    longitude = fields.Float(digits=(10, 7))
    source_url = fields.Char(string="Source URL")
    matched_at = fields.Datetime(default=fields.Datetime.now, index=True)
    evidence_json = fields.Json(string="Match Evidence")
    active = fields.Boolean(default=True, index=True)

    _company_lead_source_parcel_unique = models.Constraint(
        "unique(company_id, crm_lead_id, source_key, parcel_account)",
        "A parcel account can only be linked once per company, CRM lead, and source.",
    )
    _confidence_range = models.Constraint(
        "CHECK(confidence >= 0.0 AND confidence <= 1.0)",
        "Confidence must be between 0.0 and 1.0.",
    )

    @api.onchange("crm_lead_id")
    def _onchange_crm_lead_id(self):
        for link in self:
            if link.crm_lead_id:
                link.company_id = link.crm_lead_id.company_id or self.env.company
                link.partner_id = link.crm_lead_id.partner_id

    def action_open_source(self):
        self.ensure_one()
        if not self.source_url:
            return False
        return {
            "type": "ir.actions.act_url",
            "url": self.source_url,
            "target": "new",
        }
