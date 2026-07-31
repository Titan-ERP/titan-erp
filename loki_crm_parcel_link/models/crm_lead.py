from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class CrmLead(models.Model):
    _inherit = "crm.lead"

    parcel_link_ids = fields.One2many(
        "loki.crm.parcel.link",
        "crm_lead_id",
        string="Parcel Links",
    )
    primary_parcel_link_id = fields.Many2one(
        "loki.crm.parcel.link",
        string="Primary Parcel Link",
        compute="_compute_primary_parcel_link_id",
        inverse="_inverse_primary_parcel_link_id",
        store=True,
        readonly=False,
        check_company=True,
        domain="[('crm_lead_id', '=', id)]",
    )
    parcel_match_count = fields.Integer(
        string="Parcel Matches",
        compute="_compute_parcel_match_count",
    )

    @api.depends("parcel_link_ids", "parcel_link_ids.review_state")
    def _compute_parcel_match_count(self):
        grouped = self.env["loki.crm.parcel.link"]._read_group(
            [("crm_lead_id", "in", self.ids), ("review_state", "=", "matched")],
            ["crm_lead_id"],
            ["__count"],
        )
        counts = {lead.id: count for lead, count in grouped}
        for lead in self:
            lead.parcel_match_count = counts.get(lead.id, 0)

    @api.depends("parcel_link_ids", "parcel_link_ids.review_state", "parcel_link_ids.confidence")
    def _compute_primary_parcel_link_id(self):
        for lead in self:
            current = lead.primary_parcel_link_id
            if current and current in lead.parcel_link_ids:
                continue
            matched = lead.parcel_link_ids.filtered(
                lambda link: link.review_state == "matched"
            ).sorted(key=lambda link: (link.confidence, link.id), reverse=True)
            lead.primary_parcel_link_id = matched[:1]

    def _inverse_primary_parcel_link_id(self):
        # The stored value is user-selectable; this inverse intentionally preserves it.
        return

    @api.constrains("primary_parcel_link_id")
    def _check_primary_parcel_link_belongs_to_lead(self):
        for lead in self:
            if (
                lead.primary_parcel_link_id
                and lead.primary_parcel_link_id.crm_lead_id != lead
            ):
                raise ValidationError(
                    _("The primary parcel link must belong to this CRM record.")
                )

    def action_open_parcel_links(self):
        self.ensure_one()
        action = self.env.ref(
            "loki_crm_parcel_link.action_loki_crm_parcel_links"
        ).read()[0]
        action["domain"] = [("crm_lead_id", "=", self.id)]
        action["context"] = {
            "default_crm_lead_id": self.id,
            "default_partner_id": self.partner_id.id,
            "default_company_id": (self.company_id or self.env.company).id,
        }
        return action
