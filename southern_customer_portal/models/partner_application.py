from odoo import api, fields, models


class SouthernPartnerApplication(models.Model):
    _name = "southern.partner.application"
    _description = "Southern Equipment Partner Application"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    partner_id = fields.Many2one("res.partner", index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    state = fields.Selection(
        [
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("rejected", "Rejected"),
        ],
        default="submitted",
        required=True,
        tracking=True,
    )

    business_name = fields.Char(required=True, tracking=True)
    contact_name = fields.Char(required=True, tracking=True)
    email = fields.Char(required=True, tracking=True)
    phone = fields.Char(required=True)
    website = fields.Char()
    business_type = fields.Selection(
        [
            ("diesel_shop", "Diesel Shop"),
            ("parts_store", "Parts Store"),
            ("fleet", "Fleet"),
            ("reseller", "Reseller"),
            ("other", "Other"),
        ],
        default="diesel_shop",
        required=True,
        tracking=True,
    )
    tax_exempt = fields.Boolean()
    requested_terms = fields.Boolean(string="Requests Account Terms")
    expected_monthly_spend = fields.Monetary()
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    requested_catalog_access = fields.Boolean(default=True)
    requested_partner_pricing = fields.Boolean(default=True)
    portal_ip = fields.Char(readonly=True)
    notes = fields.Text()

    @api.model_create_multi
    def create(self, vals_list):
        applications = super().create(vals_list)
        for application in applications:
            if application.name == "New":
                application.name = f"SEC Partner - {application.business_name}"
        return applications

    def action_approve(self):
        for application in self:
            partner = application._ensure_partner()
            application.write({"state": "approved", "partner_id": partner.id})
            application._assign_partner_pricelist(partner)

    def action_activate(self):
        for application in self:
            partner = application._ensure_partner()
            application.write({"state": "active", "partner_id": partner.id})
            application._assign_partner_pricelist(partner)

    def action_suspend(self):
        self.write({"state": "suspended"})

    def action_reject(self):
        self.write({"state": "rejected"})

    def _ensure_partner(self):
        self.ensure_one()
        if self.partner_id:
            return self.partner_id

        Partner = self.env["res.partner"].sudo()
        partner = Partner.search([("email", "=ilike", self.email)], limit=1)
        if partner:
            return partner.commercial_partner_id

        return Partner.create(
            {
                "name": self.business_name,
                "company_type": "company",
                "email": self.email,
                "phone": self.phone,
                "website": self.website,
                "comment": (
                    f"Partner application contact: {self.contact_name}\n"
                    f"Business type: {dict(self._fields['business_type'].selection).get(self.business_type)}"
                ),
            }
        )

    def _assign_partner_pricelist(self, partner):
        pricelist = self.env.ref(
            "southern_parts_intelligence.southern_partner_pricelist",
            raise_if_not_found=False,
        )
        if not pricelist:
            pricelist = self.env["product.pricelist"].sudo().search(
                [("name", "=", "Southern Partner Pricing")],
                limit=1,
            )
        if pricelist and "property_product_pricelist" in partner._fields:
            partner.sudo().property_product_pricelist = pricelist
