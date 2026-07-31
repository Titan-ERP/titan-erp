import base64
import csv
import html
import io
import re

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


BROKER_GROUPS = (
    "southern_equipment_brokerage.group_southern_equipment_admin,"
    "southern_equipment_brokerage.group_southern_deal_broker"
)
ADMIN_GROUP = "southern_equipment_brokerage.group_southern_equipment_admin"


def _slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "equipment-opportunity"


def _facebook_listing_id(value):
    match = re.fullmatch(
        r"https://(?:www\.)?facebook\.com/marketplace/item/(\d+)/?",
        (value or "").strip(),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else False


class SouthernEquipmentListing(models.Model):
    _name = "southern.equipment.listing"
    _description = "Southern Equipment Sourced Listing"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "deal_score desc, create_date desc, id desc"
    _rec_name = "public_title"

    name = fields.Char(
        string="Internal Reference",
        default=lambda self: _("New"),
        required=True,
        copy=False,
        tracking=True,
        groups=BROKER_GROUPS,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    broker_id = fields.Many2one(
        "res.users",
        string="Responsible Broker",
        domain=[("share", "=", False)],
        tracking=True,
        groups=BROKER_GROUPS,
    )

    source = fields.Selection(
        [
            ("facebook_marketplace", "Facebook Marketplace"),
            ("machinerytrader", "MachineryTrader"),
            ("auctionvalues", "AuctionValues"),
            ("vip", "VIP"),
            ("dealer", "Dealer"),
            ("auction", "Auction"),
            ("manual", "Manual"),
            ("other", "Other"),
        ],
        default="manual",
        required=True,
        tracking=True,
        groups=BROKER_GROUPS,
    )
    source_url = fields.Char(groups=BROKER_GROUPS)
    source_listing_id = fields.Char(groups=BROKER_GROUPS)
    source_link_valid = fields.Boolean(
        string="Source Link Valid",
        compute="_compute_source_link_valid",
        store=True,
        index=True,
        groups=BROKER_GROUPS,
        help="Facebook records are valid only when the canonical Marketplace URL matches the source listing ID.",
    )
    capture_run_id = fields.Char(groups=BROKER_GROUPS)
    raw_capture_text = fields.Text(groups=BROKER_GROUPS)
    source_seller_id = fields.Many2one(
        "res.partner",
        string="Source Seller",
        tracking=True,
        groups=BROKER_GROUPS,
    )
    seller_name_raw = fields.Char(groups=BROKER_GROUPS)
    seller_phone = fields.Char(groups=BROKER_GROUPS)
    seller_email = fields.Char(groups=BROKER_GROUPS)
    seller_facebook = fields.Char(groups=BROKER_GROUPS)
    seller_ask_price = fields.Monetary(groups=BROKER_GROUPS)
    seller_exact_location = fields.Char(groups=BROKER_GROUPS)
    internal_notes = fields.Html(groups=BROKER_GROUPS)

    public_title = fields.Char(required=True, translate=True, tracking=True)
    public_slug = fields.Char(copy=False, index=True, groups=BROKER_GROUPS)
    public_description = fields.Html(translate=True)
    public_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("needs_verification", "Needs Verification"),
            ("published", "Published"),
            ("inquiry_received", "Inquiry Received"),
            ("verification_in_progress", "Verification In Progress"),
            ("seller_confirmed", "Seller Confirmed"),
            ("under_negotiation", "Under Negotiation"),
            ("under_contract", "Under Contract"),
            ("assigned", "Assigned"),
            ("unavailable", "Unavailable"),
            ("sold", "Sold"),
            ("archived", "Archived"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    is_southern_owned = fields.Boolean(
        string="Southern-Owned Inventory",
        tracking=True,
        groups=BROKER_GROUPS,
    )
    public_region = fields.Char(tracking=True)
    equipment_type = fields.Selection(
        [
            ("skid_steer", "Skid Steer"),
            ("dozer", "Dozer"),
            ("excavator", "Excavator"),
            ("mini_excavator", "Mini Excavator"),
            ("telehandler", "Telehandler"),
            ("forklift", "Forklift"),
            ("tractor", "Tractor"),
            ("loader", "Loader"),
            ("other", "Other"),
        ],
        default="other",
        required=True,
        tracking=True,
    )
    manufacturer = fields.Char(index=True)
    model = fields.Char(index=True)
    year = fields.Integer()
    hours = fields.Float()
    vin_serial = fields.Char(string="VIN / Serial", groups=BROKER_GROUPS)
    show_vin_serial_publicly = fields.Boolean(groups=BROKER_GROUPS)
    public_price = fields.Monetary(tracking=True)
    estimated_market_value = fields.Monetary()
    deal_score = fields.Float(help="Internal 0–100 opportunity score.")
    public_deal_summary = fields.Char(
        string="Why It Looks Like a Deal",
        help="Short public-safe explanation; do not include seller strategy or internal margin.",
    )
    verification_note = fields.Char(
        help="Public-safe verification statement. Do not include seller identity or exact location."
    )
    inspection_available = fields.Boolean(default=True)
    deposit_required = fields.Boolean()
    deposit_public_note = fields.Char(
        default="A deposit or inspection authorization may be required before inspection or negotiation."
    )
    website_published = fields.Boolean(
        string="Published on Website",
        tracking=True,
        index=True,
    )
    photo_rights_confirmed = fields.Boolean(
        string="Photo Rights Confirmed",
        groups=BROKER_GROUPS,
    )
    photo_source_note = fields.Char(
        string="Photo Source / License Note",
        groups=BROKER_GROUPS,
    )
    image_is_representative = fields.Boolean(
        string="Representative / Generic Image",
        groups=BROKER_GROUPS,
    )
    website_url = fields.Char(compute="_compute_website_url", groups=BROKER_GROUPS)
    image_1920 = fields.Image(string="Primary Photo", max_width=1920, max_height=1920)
    photo_ids = fields.Many2many(
        "ir.attachment",
        "southern_listing_attachment_rel",
        "listing_id",
        "attachment_id",
        string="Additional Photos / Media",
    )

    ask_price = fields.Monetary(groups=BROKER_GROUPS)
    expected_resale = fields.Monetary(groups=BROKER_GROUPS)
    comp_median = fields.Monetary(groups=BROKER_GROUPS)
    comp_low = fields.Monetary(groups=BROKER_GROUPS)
    comp_high = fields.Monetary(groups=BROKER_GROUPS)
    comp_count = fields.Integer(groups=BROKER_GROUPS)
    comp_confidence = fields.Selection(
        [("low", "Low"), ("medium", "Medium"), ("high", "High")],
        groups=BROKER_GROUPS,
    )
    freight_cost = fields.Monetary(groups=BROKER_GROUPS)
    repairs = fields.Monetary(groups=BROKER_GROUPS)
    inspection_estimate = fields.Monetary(groups=BROKER_GROUPS)
    landed_cost = fields.Monetary(
        compute="_compute_deal_math",
        store=True,
        groups=BROKER_GROUPS,
    )
    target_buy_price = fields.Monetary(groups=BROKER_GROUPS)
    max_offer = fields.Monetary(groups=BROKER_GROUPS)
    projected_savings = fields.Monetary(
        compute="_compute_deal_math",
        store=True,
        groups=BROKER_GROUPS,
    )
    projected_profit = fields.Monetary(
        compute="_compute_deal_math",
        store=True,
        groups=BROKER_GROUPS,
    )
    margin_pct = fields.Float(
        compute="_compute_deal_math",
        store=True,
        groups=BROKER_GROUPS,
    )
    grade = fields.Selection(
        [
            ("strong", "Strong Deal"),
            ("good", "Good Deal"),
            ("verify", "Needs Verification"),
            ("pass", "Pass"),
        ],
        default="verify",
        tracking=True,
        groups=BROKER_GROUPS,
    )
    inquiry_count = fields.Integer(compute="_compute_related_counts")
    deal_count = fields.Integer(compute="_compute_related_counts", groups=BROKER_GROUPS)

    _public_slug_unique = models.Constraint(
        "unique(public_slug)",
        "The public URL slug must be unique.",
    )
    _year_reasonable = models.Constraint(
        "CHECK(year IS NULL OR year = 0 OR (year >= 1900 AND year <= 2200))",
        "Enter a valid equipment year.",
    )
    _hours_nonnegative = models.Constraint(
        "CHECK(hours IS NULL OR hours >= 0)",
        "Hours cannot be negative.",
    )

    @api.depends("ask_price", "freight_cost", "repairs", "inspection_estimate", "expected_resale", "comp_median")
    def _compute_deal_math(self):
        for listing in self:
            listing.landed_cost = (
                listing.ask_price
                + listing.freight_cost
                + listing.repairs
                + listing.inspection_estimate
            )
            value = listing.expected_resale or listing.comp_median
            listing.projected_savings = max(value - listing.landed_cost, 0.0) if value else 0.0
            listing.projected_profit = value - listing.landed_cost if value else 0.0
            listing.margin_pct = (listing.projected_profit / value * 100.0) if value else 0.0

    @api.depends("public_slug")
    def _compute_website_url(self):
        for listing in self:
            listing.website_url = (
                f"/equipment-opportunities/{listing.public_slug}" if listing.public_slug else False
            )

    @api.depends("source", "source_url", "source_listing_id")
    def _compute_source_link_valid(self):
        for listing in self:
            if listing.source != "facebook_marketplace":
                listing.source_link_valid = True
                continue
            url_listing_id = _facebook_listing_id(listing.source_url)
            listing.source_link_valid = bool(
                url_listing_id
                and listing.source_listing_id
                and url_listing_id == listing.source_listing_id.strip()
            )

    def _compute_related_counts(self):
        Inquiry = self.env["southern.buyer.inquiry"]
        Deal = self.env["southern.brokered.deal"]
        for listing in self:
            listing.inquiry_count = Inquiry.search_count([("listing_id", "=", listing.id)])
            listing.deal_count = Deal.search_count([("listing_id", "=", listing.id)])

    def _unique_slug(self, title):
        base = _slugify(title)
        slug = base
        counter = 2
        while self.search_count([("public_slug", "=", slug)]):
            slug = f"{base}-{counter}"
            counter += 1
        return slug

    @api.model_create_multi
    def create(self, vals_list):
        reserved_slugs = set(
            self.search([("public_slug", "!=", False)]).mapped("public_slug")
        )
        for vals in vals_list:
            if not vals.get("public_slug"):
                base = _slugify(vals.get("public_title"))
                slug = base
                counter = 2
                while slug in reserved_slugs:
                    slug = f"{base}-{counter}"
                    counter += 1
                vals["public_slug"] = slug
                reserved_slugs.add(slug)
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southern.equipment.listing"
                ) or _("New")
        return super().create(vals_list)

    def write(self, vals):
        if vals.get("public_slug"):
            vals["public_slug"] = _slugify(vals["public_slug"])
        return super().write(vals)

    @api.constrains(
        "website_published",
        "public_status",
        "public_title",
        "public_region",
        "public_price",
        "public_description",
        "image_1920",
        "photo_rights_confirmed",
        "photo_source_note",
        "source_link_valid",
    )
    def _check_publish_readiness(self):
        for listing in self:
            if not listing.website_published:
                continue
            if listing.public_status in ("draft", "archived"):
                raise ValidationError(
                    _("A website listing needs a non-draft, non-archived status.")
                )
            missing = []
            if not listing.public_region:
                missing.append(_("public region"))
            if not listing.public_price:
                missing.append(_("listed price"))
            if not listing.public_description:
                missing.append(_("public description"))
            if not listing.image_1920:
                missing.append(_("primary image"))
            if not listing.photo_rights_confirmed:
                missing.append(_("confirmed photo rights"))
            if not listing.photo_source_note:
                missing.append(_("photo source/license note"))
            if listing.source == "facebook_marketplace" and not listing.source_link_valid:
                missing.append(_("matching canonical Facebook Marketplace link"))
            if missing:
                raise ValidationError(
                    _("Complete these fields before publishing: %s.")
                    % ", ".join(missing)
                )

    def action_publish(self):
        for listing in self:
            values = {"website_published": True}
            if listing.public_status == "draft":
                values["public_status"] = "needs_verification"
            listing.write(values)

    def action_unpublish(self):
        self.write({"website_published": False})

    def action_view_inquiries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Buyer Inquiries"),
            "res_model": "southern.buyer.inquiry",
            "view_mode": "kanban,list,form",
            "domain": [("listing_id", "=", self.id)],
            "context": {"default_listing_id": self.id},
        }

    def action_view_deals(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Brokered Deals"),
            "res_model": "southern.brokered.deal",
            "view_mode": "kanban,list,form",
            "domain": [("listing_id", "=", self.id)],
        }


class SouthernBuyerInquiry(models.Model):
    _name = "southern.buyer.inquiry"
    _description = "Southern Equipment Buyer Inquiry"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), required=True, copy=False, tracking=True)
    listing_id = fields.Many2one(
        "southern.equipment.listing",
        required=True,
        ondelete="restrict",
        index=True,
        tracking=True,
    )
    partner_id = fields.Many2one(
        "res.partner", string="Buyer", tracking=True, groups=BROKER_GROUPS
    )
    contact_name = fields.Char(string="Contact Name", required=True)
    phone = fields.Char(required=True, groups=BROKER_GROUPS)
    email = fields.Char(required=True, index=True, groups=BROKER_GROUPS)
    company = fields.Char(groups=BROKER_GROUPS)
    buyer_location = fields.Char()
    budget = fields.Monetary(groups=BROKER_GROUPS)
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    timeline = fields.Selection(
        [
            ("immediate", "Immediately"),
            ("30_days", "Within 30 Days"),
            ("90_days", "Within 90 Days"),
            ("researching", "Researching"),
        ]
    )
    financing_needed = fields.Boolean(groups=BROKER_GROUPS)
    trade_in = fields.Boolean(groups=BROKER_GROUPS)
    message = fields.Text(groups=BROKER_GROUPS)
    stage = fields.Selection(
        [
            ("new", "New"),
            ("contacted", "Contacted"),
            ("qualified", "Qualified"),
            ("deposit_requested", "Deposit Requested"),
            ("deposit_received", "Deposit Received"),
            ("active_deal", "Active Deal"),
            ("closed", "Closed"),
            ("lost", "Lost"),
        ],
        default="new",
        required=True,
        index=True,
        tracking=True,
    )
    broker_id = fields.Many2one(
        "res.users",
        domain=[("share", "=", False)],
        tracking=True,
    )
    crm_lead_id = fields.Many2one(
        "crm.lead",
        string="CRM Opportunity",
        readonly=True,
        copy=False,
        groups=BROKER_GROUPS,
    )
    deal_id = fields.Many2one(
        "southern.brokered.deal",
        compute="_compute_deal_id",
        string="Brokered Deal",
    )

    @api.depends("listing_id")
    def _compute_deal_id(self):
        Deal = self.env["southern.brokered.deal"]
        for inquiry in self:
            inquiry.deal_id = Deal.search([("buyer_inquiry_id", "=", inquiry.id)], limit=1)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southern.buyer.inquiry"
                ) or _("New")
        inquiries = super().create(vals_list)
        for inquiry in inquiries:
            if inquiry.listing_id.public_status == "published":
                inquiry.listing_id.public_status = "inquiry_received"
        return inquiries

    @api.model
    def create_from_website(self, listing, values, broker=False):
        """Create the complete internal follow-up chain for a validated public request."""
        Partner = self.env["res.partner"]
        partner = Partner.search([("email", "=ilike", values["email"])], limit=1)
        if not partner:
            partner = Partner.create(
                {
                    "name": values["contact_name"],
                    "email": values["email"],
                    "phone": values["phone"],
                    "company_name": values.get("company"),
                }
            )
        inquiry = self.create(
            {
                **values,
                "listing_id": listing.id,
                "partner_id": partner.id,
                "broker_id": broker.id if broker else False,
            }
        )
        lead = self.env["crm.lead"].create(
            {
                "name": f"Equipment Deal Request: {listing.public_title}",
                "type": "opportunity",
                "partner_id": partner.id,
                "contact_name": values["contact_name"],
                "email_from": values["email"],
                "phone": values["phone"],
                "user_id": broker.id if broker else False,
                "description": Markup(
                    "<p>Public inquiry for <strong>%s</strong> (%s).</p><p>%s</p>"
                )
                % (
                    listing.public_title,
                    listing.name,
                    values.get("message") or "",
                ),
            }
        )
        inquiry.crm_lead_id = lead
        if broker:
            inquiry.activity_schedule(
                "mail.mail_activity_data_call",
                user_id=broker.id,
                summary="Contact equipment buyer",
                note=f"New website inquiry for {listing.public_title}.",
            )
        return inquiry

    def action_mark_contacted(self):
        self.write({"stage": "contacted"})

    def action_qualify(self):
        self.write({"stage": "qualified"})

    def action_create_deal(self):
        self.ensure_one()
        if self.deal_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "southern.brokered.deal",
                "res_id": self.deal_id.id,
                "view_mode": "form",
            }
        if not self.partner_id:
            self.partner_id = self.env["res.partner"].create(
                {
                    "name": self.contact_name,
                    "phone": self.phone,
                    "email": self.email,
                    "company_name": self.company,
                }
            )
        deal = self.env["southern.brokered.deal"].create(
            {
                "listing_id": self.listing_id.id,
                "buyer_inquiry_id": self.id,
                "buyer_id": self.partner_id.id,
                "seller_id": self.listing_id.source_seller_id.id,
                "broker_id": self.broker_id.id or self.listing_id.broker_id.id,
                "buyer_budget": self.budget,
                "seller_ask_price": self.listing_id.seller_ask_price
                or self.listing_id.ask_price,
            }
        )
        self.write({"stage": "active_deal"})
        return {
            "type": "ir.actions.act_window",
            "res_model": "southern.brokered.deal",
            "res_id": deal.id,
            "view_mode": "form",
        }

    def action_open_crm(self):
        self.ensure_one()
        if not self.crm_lead_id:
            raise UserError(_("No CRM opportunity is linked to this inquiry."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "crm.lead",
            "res_id": self.crm_lead_id.id,
            "view_mode": "form",
        }


class SouthernBrokeredDeal(models.Model):
    _name = "southern.brokered.deal"
    _description = "Southern Equipment Brokered Deal"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), required=True, copy=False, tracking=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(
        "res.currency", required=True, default=lambda self: self.env.company.currency_id
    )
    listing_id = fields.Many2one(
        "southern.equipment.listing", required=True, ondelete="restrict", tracking=True
    )
    buyer_inquiry_id = fields.Many2one(
        "southern.buyer.inquiry", ondelete="restrict", tracking=True
    )
    buyer_id = fields.Many2one("res.partner", required=True, tracking=True)
    seller_id = fields.Many2one("res.partner", required=True, tracking=True, groups=BROKER_GROUPS)
    broker_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user, tracking=True
    )
    stage = fields.Selection(
        [
            ("buyer_qualified", "Buyer Qualified"),
            ("deposit_pending", "Deposit Pending"),
            ("deposit_received", "Deposit Received"),
            ("seller_verification", "Seller Verification"),
            ("inspection_ordered", "Inspection Ordered"),
            ("inspection_complete", "Inspection Complete"),
            ("contract_negotiation", "Contract Negotiation"),
            ("under_contract", "Under Contract"),
            ("assignment_ready", "Assignment Ready"),
            ("assigned", "Assigned to Buyer"),
            ("closed", "Closed"),
            ("lost", "Lost"),
            ("refunded", "Refunded"),
        ],
        default="buyer_qualified",
        required=True,
        index=True,
        tracking=True,
    )
    buyer_budget = fields.Monetary()
    buyer_max_price = fields.Monetary()
    seller_ask_price = fields.Monetary(groups=BROKER_GROUPS)
    negotiated_purchase_price = fields.Monetary(groups=BROKER_GROUPS)
    assignment_fee = fields.Monetary(groups=ADMIN_GROUP)
    broker_fee_type = fields.Selection(
        [
            ("flat", "Flat Fee"),
            ("percentage", "Percentage"),
            ("spread", "Spread"),
            ("buyer_paid", "Buyer Paid"),
            ("seller_paid", "Seller Paid"),
            ("other", "Other"),
        ],
        groups=ADMIN_GROUP,
    )
    expected_fee = fields.Monetary(groups=ADMIN_GROUP)
    inspection_budget = fields.Monetary(groups=ADMIN_GROUP)
    deposit_required = fields.Monetary(groups=ADMIN_GROUP)
    deposit_received = fields.Monetary(
        compute="_compute_ledger_totals", groups=ADMIN_GROUP
    )
    deposit_balance = fields.Monetary(
        compute="_compute_ledger_totals", groups=ADMIN_GROUP
    )
    deposit_received_date = fields.Date(groups=ADMIN_GROUP)
    deposit_holder = fields.Selection(
        [
            ("southern", "Southern Equipment"),
            ("escrow", "Escrow"),
            ("processor", "Payment Processor"),
            ("other", "Other"),
        ],
        groups=ADMIN_GROUP,
    )
    deposit_status = fields.Selection(
        [
            ("not_requested", "Not Requested"),
            ("requested", "Requested"),
            ("received", "Received"),
            ("partially_spent", "Partially Spent"),
            ("refunded", "Refunded"),
            ("applied", "Applied to Closing"),
            ("forfeited", "Forfeited"),
        ],
        default="not_requested",
        required=True,
        tracking=True,
        groups=ADMIN_GROUP,
    )
    inspection_order_id = fields.Many2one(
        "southern.inspection.order", compute="_compute_operation_links"
    )
    contract_id = fields.Many2one(
        "southern.contract.assignment",
        compute="_compute_operation_links",
        string="Contract / Assignment",
        groups=BROKER_GROUPS,
    )
    assignment_id = fields.Many2one(
        "southern.contract.assignment",
        compute="_compute_operation_links",
        string="Assignment",
        groups=BROKER_GROUPS,
    )
    ledger_ids = fields.One2many(
        "southern.deposit.ledger", "deal_id", string="Deposit Ledger", groups=ADMIN_GROUP
    )
    all_parties_approved = fields.Boolean(tracking=True, groups=BROKER_GROUPS)
    close_date = fields.Date(tracking=True)
    lost_reason = fields.Text()
    notes = fields.Html()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southern.brokered.deal"
                ) or _("New")
        return super().create(vals_list)

    def _compute_operation_links(self):
        Inspection = self.env["southern.inspection.order"]
        Assignment = self.env["southern.contract.assignment"]
        for deal in self:
            deal.inspection_order_id = Inspection.search([("deal_id", "=", deal.id)], limit=1)
            assignment = Assignment.search([("deal_id", "=", deal.id)], limit=1)
            deal.contract_id = assignment
            deal.assignment_id = assignment

    def _compute_ledger_totals(self):
        for deal in self:
            deposits = sum(
                deal.ledger_ids.filtered(
                    lambda row: row.transaction_type == "deposit" and row.status == "posted"
                ).mapped("amount")
            )
            deductions = sum(
                deal.ledger_ids.filtered(
                    lambda row: row.transaction_type
                    in ("inspection_spend", "refund", "applied_to_closing", "fee")
                    and row.status == "posted"
                ).mapped("amount")
            )
            adjustments = sum(
                deal.ledger_ids.filtered(
                    lambda row: row.transaction_type == "adjustment" and row.status == "posted"
                ).mapped("amount")
            )
            deal.deposit_received = deposits
            deal.deposit_balance = deposits - deductions + adjustments

    def action_request_deposit(self):
        self.write({"stage": "deposit_pending", "deposit_status": "requested"})

    def action_create_inspection(self):
        self.ensure_one()
        if self.inspection_order_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "southern.inspection.order",
                "res_id": self.inspection_order_id.id,
                "view_mode": "form",
            }
        inspection = self.env["southern.inspection.order"].create(
            {"deal_id": self.id, "listing_id": self.listing_id.id}
        )
        # Inspector coordinators can launch this narrow transition while the
        # surrounding deal remains read-only to them.
        self.sudo().stage = "inspection_ordered"
        return {
            "type": "ir.actions.act_window",
            "res_model": "southern.inspection.order",
            "res_id": inspection.id,
            "view_mode": "form",
        }

    def action_create_assignment(self):
        self.ensure_one()
        if self.contract_id:
            assignment = self.contract_id
        else:
            assignment = self.env["southern.contract.assignment"].create(
                {
                    "deal_id": self.id,
                    "listing_id": self.listing_id.id,
                    "buyer_id": self.buyer_id.id,
                    "seller_id": self.seller_id.id,
                    "assignment_fee": self.assignment_fee,
                }
            )
        return {
            "type": "ir.actions.act_window",
            "res_model": "southern.contract.assignment",
            "res_id": assignment.id,
            "view_mode": "form",
        }

    def action_close(self):
        for deal in self:
            if not deal.all_parties_approved:
                raise UserError(_("Record all-party approval before closing the deal."))
            deal.write({"stage": "closed", "close_date": fields.Date.context_today(deal)})
            deal.listing_id.write({"public_status": "sold", "website_published": False})
            if deal.buyer_inquiry_id:
                deal.buyer_inquiry_id.stage = "closed"


class SouthernInspectionOrder(models.Model):
    _name = "southern.inspection.order"
    _description = "Southern Equipment Inspection Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "scheduled_datetime desc, id desc"

    name = fields.Char(default=lambda self: _("New"), required=True, copy=False)
    deal_id = fields.Many2one(
        "southern.brokered.deal", required=True, ondelete="cascade", tracking=True
    )
    listing_id = fields.Many2one(
        "southern.equipment.listing", required=True, ondelete="restrict", tracking=True
    )
    inspection_vendor_id = fields.Many2one("res.partner", tracking=True)
    scheduled_datetime = fields.Datetime(tracking=True)
    inspection_location = fields.Char(tracking=True)
    currency_id = fields.Many2one(
        "res.currency", required=True, default=lambda self: self.env.company.currency_id
    )
    inspection_fee = fields.Monetary(tracking=True)
    deposit_amount_allocated = fields.Monetary(groups=ADMIN_GROUP)
    status = fields.Selection(
        [
            ("draft", "Draft"),
            ("ordered", "Ordered"),
            ("scheduled", "Scheduled"),
            ("complete", "Complete"),
            ("failed", "Failed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
    )
    report_file = fields.Binary(attachment=True)
    report_filename = fields.Char()
    summary = fields.Html()
    pass_fail = fields.Selection(
        [("pass", "Pass"), ("conditional", "Conditional"), ("fail", "Fail")],
        tracking=True,
    )
    repair_estimate = fields.Monetary()
    photo_ids = fields.Many2many(
        "ir.attachment",
        "southern_inspection_attachment_rel",
        "inspection_id",
        "attachment_id",
        string="Inspection Photos",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southern.inspection.order"
                ) or _("New")
        return super().create(vals_list)

    def action_mark_ordered(self):
        self.write({"status": "ordered"})

    def action_mark_complete(self):
        for order in self:
            if not order.report_file and not order.summary:
                raise UserError(_("Add an inspection report or summary before completing."))
            order.status = "complete"
            # Inspectors deliberately have read-only deal access. This narrow workflow
            # transition is the only deal mutation performed on their behalf.
            order.deal_id.sudo().stage = "inspection_complete"


class SouthernContractAssignment(models.Model):
    _name = "southern.contract.assignment"
    _description = "Southern Equipment Contract Assignment"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(default=lambda self: _("New"), required=True, copy=False)
    deal_id = fields.Many2one(
        "southern.brokered.deal", required=True, ondelete="cascade", tracking=True
    )
    listing_id = fields.Many2one(
        "southern.equipment.listing", required=True, ondelete="restrict", tracking=True
    )
    buyer_id = fields.Many2one("res.partner", required=True, tracking=True)
    seller_id = fields.Many2one(
        "res.partner", required=True, tracking=True, groups=BROKER_GROUPS
    )
    purchase_contract_file = fields.Binary(attachment=True, groups=BROKER_GROUPS)
    purchase_contract_filename = fields.Char(groups=BROKER_GROUPS)
    purchase_contract_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Sent"),
            ("seller_signed", "Seller Signed"),
            ("buyer_approved", "Buyer Approved"),
            ("executed", "Executed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        groups=BROKER_GROUPS,
    )
    assignment_agreement_file = fields.Binary(attachment=True, groups=BROKER_GROUPS)
    assignment_agreement_filename = fields.Char(groups=BROKER_GROUPS)
    assignment_status = fields.Selection(
        [
            ("draft", "Draft"),
            ("sent", "Sent"),
            ("buyer_signed", "Buyer Signed"),
            ("seller_consent_required", "Seller Consent Required"),
            ("seller_consented", "Seller Consented"),
            ("executed", "Executed"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        groups=BROKER_GROUPS,
    )
    seller_consent_required = fields.Boolean(tracking=True, groups=BROKER_GROUPS)
    seller_consent_received = fields.Boolean(tracking=True, groups=BROKER_GROUPS)
    buyer_approval_received = fields.Boolean(tracking=True, groups=BROKER_GROUPS)
    southern_approval_received = fields.Boolean(tracking=True, groups=BROKER_GROUPS)
    assignment_fee = fields.Monetary(groups=ADMIN_GROUP)
    currency_id = fields.Many2one(
        "res.currency", required=True, default=lambda self: self.env.company.currency_id
    )
    effective_datetime = fields.Datetime(tracking=True, groups=BROKER_GROUPS)
    notes = fields.Html(groups=BROKER_GROUPS)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code(
                    "southern.contract.assignment"
                ) or _("New")
        return super().create(vals_list)

    def action_execute(self):
        for assignment in self:
            if not assignment.buyer_approval_received or not assignment.southern_approval_received:
                raise UserError(_("Buyer and Southern Equipment approval are required."))
            if assignment.seller_consent_required and not assignment.seller_consent_received:
                raise UserError(_("Record the required seller consent before execution."))
            if not assignment.assignment_agreement_file:
                raise UserError(_("Upload the assignment agreement before execution."))
            assignment.write(
                {
                    "assignment_status": "executed",
                    "effective_datetime": fields.Datetime.now(),
                }
            )
            assignment.deal_id.write(
                {"stage": "assigned", "all_parties_approved": True}
            )
            assignment.listing_id.public_status = "assigned"


class SouthernDepositLedger(models.Model):
    _name = "southern.deposit.ledger"
    _description = "Southern Equipment Deposit Ledger"
    _order = "transaction_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    deal_id = fields.Many2one(
        "southern.brokered.deal", required=True, ondelete="cascade", index=True
    )
    buyer_id = fields.Many2one(
        "res.partner", required=True, related="deal_id.buyer_id", store=True
    )
    currency_id = fields.Many2one(
        "res.currency", required=True, related="deal_id.currency_id", store=True
    )
    amount = fields.Monetary(required=True)
    transaction_type = fields.Selection(
        [
            ("deposit", "Deposit"),
            ("inspection_spend", "Inspection Spend"),
            ("refund", "Refund"),
            ("applied_to_closing", "Applied to Closing"),
            ("fee", "Fee"),
            ("adjustment", "Adjustment"),
        ],
        required=True,
        default="deposit",
    )
    transaction_date = fields.Date(required=True, default=fields.Date.context_today)
    payment_reference = fields.Char()
    held_by = fields.Selection(
        [
            ("southern", "Southern Equipment"),
            ("escrow", "Escrow"),
            ("processor", "Payment Processor"),
            ("other", "Other"),
        ]
    )
    status = fields.Selection(
        [("draft", "Draft"), ("posted", "Posted"), ("void", "Void")],
        default="draft",
        required=True,
    )
    notes = fields.Text()

    @api.depends("deal_id", "transaction_type", "transaction_date")
    def _compute_name(self):
        labels = dict(self._fields["transaction_type"].selection)
        for row in self:
            row.name = (
                f"{row.deal_id.name or ''} - {labels.get(row.transaction_type, '')} "
                f"{row.transaction_date or ''}"
            ).strip()

    @api.constrains("amount", "transaction_type")
    def _check_amount(self):
        for row in self:
            if row.amount < 0 and row.transaction_type != "adjustment":
                raise ValidationError(_("Use a positive amount; the transaction type controls direction."))

    def action_post(self):
        for row in self:
            if row.status != "draft":
                continue
            row.status = "posted"
            if row.transaction_type == "deposit":
                row.deal_id.write(
                    {
                        "deposit_status": "received",
                        "stage": "deposit_received",
                        "deposit_received_date": row.transaction_date,
                    }
                )
            elif row.transaction_type == "refund":
                row.deal_id.write({"deposit_status": "refunded", "stage": "refunded"})
            elif row.transaction_type == "applied_to_closing":
                row.deal_id.deposit_status = "applied"
            elif row.transaction_type == "inspection_spend":
                row.deal_id.deposit_status = "partially_spent"

    def action_void(self):
        self.write({"status": "void"})


class SouthernEquipmentComp(models.Model):
    _name = "southern.equipment.comp"
    _description = "Southern Equipment Comparable"
    _order = "sale_date desc, id desc"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    source = fields.Char(required=True)
    source_url = fields.Char()
    equipment_type = fields.Selection(
        selection=lambda self: self.env["southern.equipment.listing"]._fields[
            "equipment_type"
        ].selection,
        required=True,
        default="other",
    )
    manufacturer = fields.Char(index=True)
    model = fields.Char(index=True)
    year = fields.Integer()
    hours = fields.Float()
    currency_id = fields.Many2one(
        "res.currency", required=True, default=lambda self: self.env.company.currency_id
    )
    price = fields.Monetary(required=True)
    sale_type = fields.Selection(
        [
            ("asking", "Asking"),
            ("auction_result", "Auction Result"),
            ("retail_value", "Retail Value"),
            ("wholesale_value", "Wholesale Value"),
            ("vip_valuation", "VIP Valuation"),
            ("manual", "Manual"),
        ],
        default="manual",
        required=True,
    )
    sale_date = fields.Date()
    location = fields.Char()
    notes = fields.Text()

    _comp_price_nonnegative = models.Constraint(
        "CHECK(price >= 0)",
        "Comparable price cannot be negative.",
    )
    _comp_hours_nonnegative = models.Constraint(
        "CHECK(hours IS NULL OR hours >= 0)",
        "Hours cannot be negative.",
    )


class SouthernEquipmentImportWizard(models.TransientModel):
    _name = "southern.equipment.import.wizard"
    _description = "Import Facebook Agent Equipment Opportunities"

    upload_file = fields.Binary(string="CSV File", required=True)
    upload_filename = fields.Char(required=True)
    update_existing = fields.Boolean(
        default=True,
        help="Update a listing when Equipment ID matches its Source Listing ID.",
    )

    def _number(self, value, integer=False):
        cleaned = re.sub(r"[^0-9.\-]", "", value or "")
        if not cleaned:
            return 0 if integer else 0.0
        try:
            number = float(cleaned)
        except ValueError:
            return 0 if integer else 0.0
        return int(number) if integer else number

    def _source_key(self, value):
        normalized = (value or "").strip().lower()
        if "facebook" in normalized:
            return "facebook_marketplace"
        if "machinerytrader" in normalized:
            return "machinerytrader"
        if "auctionvalues" in normalized:
            return "auctionvalues"
        if normalized == "vip":
            return "vip"
        if "dealer" in normalized:
            return "dealer"
        if "auction" in normalized:
            return "auction"
        if normalized in ("", "manual"):
            return "manual"
        return "other"

    def _equipment_type_key(self, value):
        normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()
        mappings = [
            ("mini excavator", "mini_excavator"),
            ("skid steer", "skid_steer"),
            ("telehandler", "telehandler"),
            ("excavator", "excavator"),
            ("bulldozer", "dozer"),
            ("dozer", "dozer"),
            ("forklift", "forklift"),
            ("tractor", "tractor"),
            ("loader", "loader"),
        ]
        for label, key in mappings:
            if label in normalized:
                return key
        return "other"

    def _public_status_key(self, value):
        normalized = (value or "").strip().lower().replace(" ", "_")
        direct = {
            key
            for key, _label in self.env["southern.equipment.listing"]._fields[
                "public_status"
            ].selection
        }
        if normalized in direct:
            return normalized
        return {
            "new": "needs_verification",
            "qualified": "verification_in_progress",
            "proposition": "under_negotiation",
        }.get(normalized, "needs_verification")

    def _raw_capture(self, notes, explicit=False):
        if explicit:
            return explicit.strip()
        marker = "Captured text:"
        if marker not in (notes or ""):
            return False
        return notes.split(marker, 1)[1].strip()

    def _text(self, row, *headers):
        for header in headers:
            value = row.get(header)
            if value not in (None, ""):
                return value.strip()
        return ""

    def _row_values(self, row):
        equipment_id = self._text(row, "Equipment ID", "Source Listing ID")
        title = (
            self._text(
                row,
                "Standardized Title",
                "Equipment Name",
                "Public Title",
                "Opportunity",
            )
            or equipment_id
            or _("Imported Equipment Opportunity")
        ).strip()
        seller = self._text(
            row, "Contact Name", "Seller Name Raw", "Seller Name", "Customer"
        )
        notes = self._text(row, "Internal Notes")
        priority = self._text(row, "Priority")
        serial = self._text(row, "VIN/Serial", "VIN / Serial")
        ask_price = self._text(row, "Seller Ask", "Ask Price", "Seller Ask Price")
        ask_price_value = self._number(ask_price)
        explicit_public_price = self._number(self._text(row, "Public Price"))
        public_price = explicit_public_price
        if not public_price and ask_price_value:
            public_price = self.env.company.currency_id.round(
                ask_price_value * 1.05
            )
        location = self._text(
            row, "Restricted Exact Location", "Location", "Seller Exact Location"
        )
        public_region = self._text(row, "Public Region")
        public_description = self._text(row, "Public Description")
        photo_rights = self._text(row, "Photo Rights Confirmed").lower()
        representative_image = self._text(
            row, "Representative/Generic Image", "Representative / Generic Image"
        ).lower()
        return {
            "name": equipment_id or _("New"),
            "public_title": title,
            "public_status": self._public_status_key(
                self._text(row, "Public Status", "Stage")
            ),
            "website_published": False,
            "source": self._source_key(self._text(row, "Source")),
            "source_url": self._text(
                row, "Canonical Source URL", "Source URL", "Facebook URL"
            ),
            "source_listing_id": equipment_id,
            "capture_run_id": self._text(row, "Capture Run ID"),
            "raw_capture_text": self._raw_capture(
                notes, self._text(row, "Raw Capture Text")
            ),
            "seller_name_raw": seller,
            "seller_phone": self._text(row, "Phone", "Seller Phone"),
            "seller_email": self._text(row, "Email", "Seller Email"),
            "seller_facebook": self._text(row, "Seller Facebook"),
            "seller_ask_price": ask_price_value,
            "seller_exact_location": location,
            "internal_notes": f"<p>{html.escape(notes)}</p>" if notes else False,
            "equipment_type": self._equipment_type_key(
                self._text(row, "Equipment Type")
            ),
            "manufacturer": self._text(row, "Manufacturer"),
            "model": self._text(row, "Model"),
            "year": self._number(self._text(row, "Year"), integer=True),
            "hours": self._number(self._text(row, "Hours")),
            "vin_serial": serial or False,
            "ask_price": ask_price_value,
            # Default the public opportunity price to a transparent 5% contingency
            # above the source ask. An explicit Public Price remains a broker override.
            "public_price": public_price,
            "public_region": public_region or False,
            "public_description": public_description or False,
            "photo_rights_confirmed": photo_rights in ("1", "true", "yes"),
            "photo_source_note": self._text(
                row, "Photo Source/License Note", "Photo Source / License Note"
            ) or False,
            "image_is_representative": representative_image in ("1", "true", "yes"),
            "expected_resale": self._number(
                self._text(row, "Expected Revenue", "Expected Resale")
            ),
            "max_offer": self._number(self._text(row, "Max Offer")),
            "deal_score": 85.0 if priority == "3" else 65.0 if priority == "2" else 50.0,
            "grade": "strong" if priority == "3" else "good" if priority == "2" else "verify",
            "verification_note": "Availability and seller information have not yet been verified.",
        }

    def action_import(self):
        self.ensure_one()
        try:
            decoded = base64.b64decode(self.upload_file).decode("utf-8-sig")
        except (ValueError, UnicodeDecodeError) as exc:
            raise UserError(_("Upload a UTF-8 CSV file.")) from exc
        reader = csv.DictReader(io.StringIO(decoded))
        headers = set(reader.fieldnames or [])
        has_id = bool(headers.intersection({"Equipment ID", "Source Listing ID"}))
        has_title = bool(
            headers.intersection(
                {"Standardized Title", "Opportunity", "Equipment Name", "Public Title"}
            )
        )
        if not reader.fieldnames or "Source" not in headers or not has_id or not has_title:
            raise UserError(
                _(
                    "This file does not match a supported Facebook Agent export. "
                    "Expected Source, an Equipment ID or Source Listing ID, and "
                    "a Standardized Title, Opportunity, Equipment Name, or Public Title."
                )
            )

        Listing = self.env["southern.equipment.listing"]
        imported = Listing.browse()
        skipped = 0
        for row_number, row in enumerate(reader, start=2):
            if not any((value or "").strip() for value in row.values()):
                continue
            vals = self._row_values(row)
            equipment_id = vals.get("source_listing_id")
            domain = [
                ("source", "=", vals["source"]),
                ("source_listing_id", "=", equipment_id),
            ]
            existing = Listing.search(domain, limit=1) if equipment_id else Listing.browse()
            if not existing and vals.get("source_url"):
                existing = Listing.search(
                    [
                        ("source", "=", vals["source"]),
                        ("source_url", "=", vals["source_url"]),
                    ],
                    limit=1,
                )
            if existing and not self.update_existing:
                skipped += 1
                continue
            try:
                if existing:
                    vals.pop("name", None)
                    existing.write(vals)
                    imported |= existing
                else:
                    imported |= Listing.create(vals)
            except (ValueError, ValidationError) as exc:
                raise UserError(_("CSV row %(row)s could not be imported: %(error)s", row=row_number, error=exc)) from exc

        if not imported:
            raise UserError(
                _("No listings were imported. %s existing rows were skipped.") % skipped
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Imported Sourced Listings"),
            "res_model": "southern.equipment.listing",
            "view_mode": "list,form",
            "domain": [("id", "in", imported.ids)],
            "context": {"search_default_needs_verification": 1},
        }
