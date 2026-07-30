from datetime import timedelta

from odoo import api, fields, models


class SouthernMembershipApplication(models.Model):
    _name = "southern.membership.application"
    _description = "Southern Equipment Membership Application"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(default="New", readonly=True, copy=False)
    partner_id = fields.Many2one("res.partner", required=True, index=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    state = fields.Selection(
        [
            ("submitted", "Submitted"),
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("cancelled", "Cancelled"),
        ],
        default="submitted",
        required=True,
        tracking=True,
    )

    member_name = fields.Char(required=True, tracking=True)
    phone = fields.Char(required=True)
    email = fields.Char(required=True)
    signed_on = fields.Datetime(default=fields.Datetime.now, readonly=True)
    signature = fields.Char(required=True)
    portal_ip = fields.Char(readonly=True)

    monthly_fee = fields.Monetary(default=25.0, required=True)
    currency_id = fields.Many2one(
        "res.currency",
        default=lambda self: self.env.company.currency_id,
        required=True,
    )
    trial_start_date = fields.Date(default=fields.Date.context_today, required=True)
    trial_end_date = fields.Date(compute="_compute_trial_end_date", store=True)
    parts_service_discount = fields.Float(default=5.0, required=True)
    house_credit_limit = fields.Monetary(default=2500.0, required=True)
    requested_house_credit = fields.Monetary(default=2500.0, required=True)
    requires_credit_approval = fields.Boolean(compute="_compute_requires_credit_approval", store=True)

    cardholder_name = fields.Char()
    billing_street = fields.Char()
    billing_zip = fields.Char()
    billing_email = fields.Char()
    billing_phone = fields.Char()
    payment_authorized = fields.Boolean(required=True)
    agreement_accepted = fields.Boolean(required=True)
    notes = fields.Text()

    @api.depends("trial_start_date")
    def _compute_trial_end_date(self):
        for application in self:
            application.trial_end_date = (
                application.trial_start_date + timedelta(days=30)
                if application.trial_start_date
                else False
            )

    @api.depends("requested_house_credit", "house_credit_limit")
    def _compute_requires_credit_approval(self):
        for application in self:
            application.requires_credit_approval = (
                application.requested_house_credit > application.house_credit_limit
            )

    @api.model_create_multi
    def create(self, vals_list):
        applications = super().create(vals_list)
        for application in applications:
            if application.name == "New":
                application.name = f"SEC Membership - {application.partner_id.display_name}"
            application._sync_partner_membership_status()
        return applications

    def action_activate(self):
        self.write({"state": "active"})
        self._sync_partner_membership_status()

    def action_suspend(self):
        self.write({"state": "suspended"})
        self._sync_partner_membership_status()

    def action_cancel(self):
        self.write({"state": "cancelled"})
        self._sync_partner_membership_status()

    def write(self, vals):
        result = super().write(vals)
        if {"state", "partner_id"} & set(vals):
            self._sync_partner_membership_status()
        return result

    def _sync_partner_membership_status(self):
        for application in self:
            partner = application.partner_id.commercial_partner_id
            if not partner:
                continue
            vals = {
                "southern_membership_status": application.state,
                "southern_membership_application_id": application.id,
            }
            if application.state == "active":
                vals["southern_account_type"] = "member"
            elif (
                application.state in ("suspended", "cancelled")
                and partner.southern_account_type == "member"
            ):
                vals["southern_account_type"] = "standard"
            partner.sudo().write(vals)
