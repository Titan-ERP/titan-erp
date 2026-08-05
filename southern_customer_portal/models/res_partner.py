from odoo import fields, models


class ResPartner(models.Model):
    _inherit = "res.partner"

    southern_synchrony_account_number = fields.Char(
        string="Synchrony Account Number",
        copy=False,
        index=True,
        tracking=True,
        help="Customer's Synchrony financing account number.",
    )
    southern_account_type = fields.Selection(
        [
            ("standard", "Standard Customer"),
            ("member", "Premium Member"),
            ("partner", "Partner / Volume Buyer"),
        ],
        default="standard",
        string="Southern Account Type",
        tracking=True,
    )
    southern_membership_status = fields.Selection(
        [
            ("none", "None"),
            ("submitted", "Submitted"),
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("cancelled", "Cancelled"),
        ],
        default="none",
        string="Membership Status",
        tracking=True,
    )
    southern_partner_status = fields.Selection(
        [
            ("none", "None"),
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("active", "Active"),
            ("suspended", "Suspended"),
            ("rejected", "Rejected"),
        ],
        default="none",
        string="Partner Status",
        tracking=True,
    )
    southern_membership_application_id = fields.Many2one(
        "southern.membership.application",
        string="Latest Membership Application",
        readonly=True,
    )
    southern_partner_application_id = fields.Many2one(
        "southern.partner.application",
        string="Latest Partner Application",
        readonly=True,
    )
