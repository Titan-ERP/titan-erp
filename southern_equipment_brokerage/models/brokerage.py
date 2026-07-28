import base64
import csv
import html
import io
import re
from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


BROKER_GROUPS = (
    "southern_equipment_brokerage.group_southern_equipment_admin,"
    "southern_equipment_brokerage.group_southern_deal_broker"
)
ADMIN_GROUP = "southern_equipment_brokerage.group_southern_equipment_admin"
PUBLIC_WEBSITE_STATUSES = (
    "needs_verification",
    "published",
    "inquiry_received",
    "verification_in_progress",
    "seller_confirmed",
    "under_negotiation",
    "under_contract",
)
TERMINAL_LISTING_STATUSES = ("assigned", "unavailable", "sold", "archived")


def _slugify(value):
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "equipment-opportunity"


def _canonical_source_url(value):
    value = (value or "").strip()
    if not value:
        return False
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return value
    path = parsed.path.rstrip("/") or "/"
    if "facebook.com" in parsed.netloc.lower():
        query = ""
    else:
        query = urlencode(
            sorted(
                (key, item)
                for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                if not key.lower().startswith("utm_")
                and key.lower() not in {"fbclid", "gclid", "mc_cid", "mc_eid"}
            ),
            doseq=True,
        )
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc.lower(), path, query, "")
    )


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
    capture_run_id = fields.Char(groups=BROKER_GROUPS)
    raw_capture_text = fields.Text(groups=BROKER_GROUPS)
    facebook_shared_url = fields.Char(
        string="Original Facebook Link",
        groups=BROKER_GROUPS,
        help="The share or Marketplace URL pasted by a broker before visible-browser enrichment.",
    )
    facebook_intake_status = fields.Selection(
        [
            ("pending", "Pending Browser Enrichment"),
            ("resolved", "Enriched"),
            ("failed", "Needs Broker Review"),
        ],
        string="Facebook Intake",
        groups=BROKER_GROUPS,
        tracking=True,
    )
    facebook_intake_requested_by = fields.Many2one(
        "res.users",
        string="Facebook Intake Requested By",
        groups=BROKER_GROUPS,
        readonly=True,
    )
    facebook_intake_requested_at = fields.Datetime(
        string="Facebook Intake Requested At",
        groups=BROKER_GROUPS,
        readonly=True,
    )
    facebook_intake_error = fields.Char(
        string="Facebook Intake Issue",
        groups=BROKER_GROUPS,
    )
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
            ("compact_track_loader", "Compact Track Loader"),
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
    estimated_market_value = fields.Monetary(groups=BROKER_GROUPS)
    deal_score = fields.Float(
        help="Internal 0–100 opportunity score.",
        groups=BROKER_GROUPS,
    )
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
    website_url = fields.Char(compute="_compute_website_url", groups=BROKER_GROUPS)
    image_1920 = fields.Image(string="Primary Photo", max_width=1920, max_height=1920)
    photo_rights_confirmed = fields.Boolean(
        string="Photo Rights Confirmed",
        groups=BROKER_GROUPS,
        help="Confirm Southern Equipment is authorized to publish every displayed photo.",
    )
    photo_source_note = fields.Char(
        string="Photo Source / License Note",
        groups=BROKER_GROUPS,
        help="Record the owned, licensed, dealer-authorized, or generated asset source.",
    )
    image_is_representative = fields.Boolean(
        string="Representative / Generic Image",
        groups=BROKER_GROUPS,
        help=(
            "Enable this when the primary image illustrates the equipment type but "
            "does not show the specific machine offered."
        ),
    )
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
    _source_listing_unique = models.Constraint(
        "unique(source, source_listing_id)",
        "A source listing ID can only be imported once for the same source.",
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
            if vals.get("source_url"):
                vals["source_url"] = _canonical_source_url(vals["source_url"])
            if vals.get("public_status") in TERMINAL_LISTING_STATUSES:
                vals["website_published"] = False
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
        if vals.get("source_url"):
            vals["source_url"] = _canonical_source_url(vals["source_url"])
        if vals.get("public_status") in TERMINAL_LISTING_STATUSES:
            vals["website_published"] = False
        return super().write(vals)

    @api.constrains(
        "website_published",
        "public_status",
        "public_title",
        "public_region",
        "verification_note",
        "image_1920",
        "photo_rights_confirmed",
        "photo_source_note",
        "show_vin_serial_publicly",
        "vin_serial",
    )
    def _check_publish_readiness(self):
        for listing in self:
            if listing.website_published and listing.public_status not in PUBLIC_WEBSITE_STATUSES:
                raise ValidationError(
                    _("A website listing needs an active, public-safe status.")
                )
            if listing.website_published and not listing.public_region:
                raise ValidationError(_("Add a public region before publishing."))
            if listing.website_published and not listing.verification_note:
                raise ValidationError(
                    _("Add a public-safe verification note before publishing.")
                )
            if listing.website_published and not listing.image_1920:
                raise ValidationError(
                    _("Add a reviewed primary photo before publishing.")
                )
            if listing.website_published and (
                not listing.photo_rights_confirmed or not listing.photo_source_note
            ):
                raise ValidationError(
                    _(
                        "Confirm publication rights and record the primary photo "
                        "source before publishing."
                    )
                )
            if listing.show_vin_serial_publicly and not listing.vin_serial:
                raise ValidationError(
                    _("Add a VIN/serial before approving it for public display.")
                )

    @api.constrains(
        "public_price",
        "estimated_market_value",
        "deal_score",
        "ask_price",
        "expected_resale",
        "comp_median",
        "comp_low",
        "comp_high",
        "freight_cost",
        "repairs",
        "inspection_estimate",
        "target_buy_price",
        "max_offer",
    )
    def _check_listing_numbers(self):
        monetary_fields = (
            "public_price",
            "estimated_market_value",
            "ask_price",
            "expected_resale",
            "comp_median",
            "comp_low",
            "comp_high",
            "freight_cost",
            "repairs",
            "inspection_estimate",
            "target_buy_price",
            "max_offer",
        )
        for listing in self:
            if any(listing[field_name] < 0 for field_name in monetary_fields):
                raise ValidationError(_("Equipment prices and costs cannot be negative."))
            if not 0 <= listing.deal_score <= 100:
                raise ValidationError(_("Deal score must be between 0 and 100."))

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
    website_submission = fields.Boolean(readonly=True, groups=BROKER_GROUPS)
    privacy_consent_at = fields.Datetime(readonly=True, groups=BROKER_GROUPS)
    privacy_notice_version = fields.Char(readonly=True, groups=BROKER_GROUPS)
    submission_fingerprint = fields.Char(
        readonly=True,
        index=True,
        groups=ADMIN_GROUP,
        help="One-way request fingerprint used only for website abuse prevention.",
    )
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
            if (
                inquiry.listing_id.website_published
                and inquiry.listing_id.public_status in PUBLIC_WEBSITE_STATUSES
            ):
                inquiry.listing_id.public_status = "inquiry_received"
        return inquiries

    @api.model
    def create_from_website(self, listing, values, broker=False):
        """Create the complete internal follow-up chain for a validated public request."""
        fingerprint = values.get("submission_fingerprint")
        if fingerprint and self.search_count(
            [
                ("submission_fingerprint", "=", fingerprint),
                ("create_date", ">=", fields.Datetime.now() - timedelta(minutes=15)),
            ]
        ) >= 20:
            return self.browse()
        cutoff = fields.Datetime.now() - timedelta(minutes=10)
        duplicate = self.search(
            [
                ("listing_id", "=", listing.id),
                ("email", "=ilike", values["email"]),
                ("create_date", ">=", cutoff),
            ],
            order="id desc",
            limit=1,
        )
        if duplicate:
            return duplicate
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
        if not self.listing_id.source_seller_id:
            raise UserError(
                _(
                    "Link the verified seller contact on the sourced listing "
                    "before creating a brokered deal."
                )
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

    @api.depends(
        "ledger_ids.amount",
        "ledger_ids.status",
        "ledger_ids.transaction_type",
    )
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
        for deal in self:
            if deal.deposit_required <= 0:
                raise UserError(
                    _("Enter a positive required deposit before requesting it.")
                )
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
            if deal.stage != "assigned":
                raise UserError(
                    _("Only a deal assigned to the buyer can be closed.")
                )
            if not deal.all_parties_approved:
                raise UserError(_("Record all-party approval before closing the deal."))
            deal.write({"stage": "closed", "close_date": fields.Date.context_today(deal)})
            listing_values = {
                "public_status": "sold",
                "website_published": False,
            }
            if deal.negotiated_purchase_price:
                listing_values["actual_sale_price"] = deal.negotiated_purchase_price
            deal.listing_id.write(listing_values)
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
            if not order.pass_fail:
                raise UserError(
                    _("Record the inspection outcome before completing the inspection.")
                )
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
            if assignment.purchase_contract_status != "executed":
                raise UserError(
                    _("Mark the purchase contract as executed before the assignment.")
                )
            if not assignment.purchase_contract_file:
                raise UserError(
                    _("Upload the executed purchase contract before the assignment.")
                )
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
            if row.transaction_type == "adjustment" and not row.amount:
                raise ValidationError(_("An adjustment amount cannot be zero."))
            if row.transaction_type != "adjustment" and row.amount <= 0:
                raise ValidationError(
                    _("Use a positive amount; the transaction type controls direction.")
                )

    def _sync_deposit_state(self):
        for deal in self.mapped("deal_id"):
            posted = deal.ledger_ids.filtered(lambda row: row.status == "posted")
            deposits = posted.filtered(lambda row: row.transaction_type == "deposit")
            deductions = posted.filtered(
                lambda row: row.transaction_type
                in ("inspection_spend", "refund", "applied_to_closing", "fee")
            )
            if not deposits:
                status = (
                    "requested"
                    if deal.deposit_status == "requested"
                    else "not_requested"
                )
            elif posted.filtered(
                lambda row: row.transaction_type == "applied_to_closing"
            ):
                status = "applied"
            elif deal.deposit_balance <= 0 and posted.filtered(
                lambda row: row.transaction_type == "refund"
            ):
                status = "refunded"
            elif deductions:
                status = "partially_spent"
            else:
                status = "received"
            deal.deposit_status = status

    def action_post(self):
        for row in self:
            if row.status != "draft":
                continue
            if (
                row.transaction_type
                in ("inspection_spend", "refund", "applied_to_closing", "fee")
                and row.amount > row.deal_id.deposit_balance
            ):
                raise UserError(
                    _("This transaction exceeds the available deposit balance.")
                )
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
        self._sync_deposit_state()

    def action_void(self):
        self.write({"status": "void"})
        self._sync_deposit_state()


class SouthernEquipmentComp(models.Model):
    _name = "southern.equipment.comp"
    _description = "Southern Equipment Comparable"
    _order = "sale_date desc, id desc"

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
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
    validate_only = fields.Boolean(
        string="Validate Only (No Database Changes)",
        default=True,
        help=(
            "Check the file, deduplication keys, and row values without creating "
            "or updating sourced listings."
        ),
    )

    MAX_FILE_BYTES = 5 * 1024 * 1024
    MAX_IMPORT_ROWS = 500

    def _number(self, value, integer=False):
        cleaned = re.sub(r"[^0-9.\-]", "", value or "")
        if not cleaned:
            return 0 if integer else 0.0
        try:
            number = float(cleaned)
        except ValueError as exc:
            raise ValueError(_("Invalid number: %s") % value) from exc
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
            ("compact track loader", "compact_track_loader"),
            ("track loader", "compact_track_loader"),
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
        return {
            "new": "needs_verification",
            "qualified": "verification_in_progress",
            "proposition": "under_negotiation",
        }.get((value or "").strip().lower(), "needs_verification")

    def _raw_capture(self, notes):
        marker = "Captured text:"
        if marker not in (notes or ""):
            return False
        return notes.split(marker, 1)[1].strip()

    def _hours(self, row, notes):
        explicit = (row.get("Hours") or "").strip()
        if explicit:
            return self._number(explicit)
        raw_text = self._raw_capture(notes) or ""
        match = re.search(r"\b([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:hours?|hrs?)\b", raw_text, re.I)
        return self._number(match.group(1)) if match else 0.0

    def _capture_run_id(self, row, notes):
        explicit = (row.get("Capture Run ID") or "").strip()
        if explicit:
            return explicit
        match = re.search(r"(?:Capture Run ID|Capture Run):\s*([^|]+)", notes or "", re.I)
        return match.group(1).strip() if match else False

    def _row_values(self, row):
        equipment_id = (row.get("Equipment ID") or "").strip()
        source_url = (
            row.get("Source URL")
            or row.get("Facebook URL")
            or ""
        ).strip()
        if not equipment_id and not source_url:
            raise ValueError(
                _("Each row needs an Equipment ID or canonical source URL.")
            )
        title = (
            row.get("Equipment Name")
            or row.get("Opportunity")
            or equipment_id
            or _("Imported Equipment Opportunity")
        ).strip()
        seller = (row.get("Contact Name") or row.get("Customer") or "").strip()
        notes = (row.get("Internal Notes") or "").strip()
        priority = (row.get("Priority") or "").strip()
        serial = (row.get("VIN/Serial") or "").strip()
        vals = {
            "name": equipment_id or _("New"),
            "public_title": title,
            "public_status": self._public_status_key(row.get("Stage")),
            "website_published": False,
            "source": self._source_key(row.get("Source")),
            "source_url": _canonical_source_url(source_url),
            "source_listing_id": equipment_id,
            "facebook_shared_url": _canonical_source_url(
                row.get("Original Facebook Link")
            ),
            "capture_run_id": self._capture_run_id(row, notes),
            "raw_capture_text": self._raw_capture(notes),
            "seller_name_raw": seller,
            "seller_phone": (row.get("Phone") or "").strip(),
            "seller_email": (row.get("Email") or "").strip(),
            "seller_facebook": (row.get("Seller Facebook") or "").strip(),
            "seller_ask_price": self._number(row.get("Ask Price")),
            "seller_exact_location": (row.get("Location") or "").strip(),
            "internal_notes": f"<p>{html.escape(notes)}</p>" if notes else False,
            "equipment_type": self._equipment_type_key(
                " ".join(
                    filter(
                        None,
                        [row.get("Equipment Type"), title],
                    )
                )
            ),
            "manufacturer": (row.get("Manufacturer") or "").strip(),
            "model": (row.get("Model") or "").strip(),
            "year": self._number(row.get("Year"), integer=True),
            "hours": self._hours(row, notes),
            "vin_serial": serial or False,
            "ask_price": self._number(row.get("Ask Price")),
            "expected_resale": self._number(row.get("Expected Revenue")),
            "max_offer": self._number(row.get("Max Offer")),
            "deal_score": 85.0 if priority == "3" else 65.0 if priority == "2" else 50.0,
            "grade": "strong" if priority == "3" else "good" if priority == "2" else "verify",
            "verification_note": "Availability and seller information have not yet been verified.",
        }
        if vals["source"] == "facebook_marketplace" and equipment_id:
            vals.update(
                {
                    "facebook_intake_status": "resolved",
                    "facebook_intake_error": False,
                }
            )
        return vals

    def _find_existing_listing(self, Listing, vals):
        equipment_id = vals.get("source_listing_id")
        source = vals.get("source")
        existing = (
            Listing.search(
                [
                    ("source", "=", source),
                    ("source_listing_id", "=", equipment_id),
                ],
                limit=1,
            )
            if equipment_id
            else Listing.browse()
        )
        if not existing and vals.get("source_url"):
            candidates = Listing.search(
                [("source", "=", source), ("source_url", "!=", False)]
            )
            existing = candidates.filtered(
                lambda listing: _canonical_source_url(listing.source_url)
                == vals["source_url"]
            )[:1]
        if not existing and vals.get("facebook_shared_url"):
            existing = Listing.search(
                [
                    ("source", "=", source),
                    ("facebook_shared_url", "=", vals["facebook_shared_url"]),
                ],
                limit=1,
            )
        return existing

    def _row_identity(self, vals):
        source = vals.get("source")
        if vals.get("source_listing_id"):
            return (source, "id", vals["source_listing_id"])
        return (source, "url", vals.get("source_url"))

    def action_import(self):
        self.ensure_one()
        if not (self.upload_filename or "").lower().endswith(".csv"):
            raise UserError(_("Upload a .csv file."))
        try:
            raw_file = base64.b64decode(self.upload_file, validate=True)
            if len(raw_file) > self.MAX_FILE_BYTES:
                raise UserError(_("The CSV file must be 5 MB or smaller."))
            decoded = raw_file.decode("utf-8-sig")
        except (ValueError, UnicodeDecodeError) as exc:
            raise UserError(_("Upload a UTF-8 CSV file.")) from exc
        reader = csv.DictReader(io.StringIO(decoded))
        expected = {"Opportunity", "Equipment ID", "Equipment Name", "Source"}
        if reader.fieldnames:
            reader.fieldnames = [field.strip() if field else field for field in reader.fieldnames]
        if (
            not reader.fieldnames
            or len(reader.fieldnames) != len(set(reader.fieldnames))
            or not expected.issubset(set(reader.fieldnames))
        ):
            raise UserError(
                _(
                    "This file does not match the Facebook Agent Odoo export. "
                    "Expected at least: %s"
                )
                % ", ".join(sorted(expected))
            )

        Listing = self.env["southern.equipment.listing"]
        imported = Listing.browse()
        created = 0
        updated = 0
        skipped = 0
        seen_rows = {}
        for row_number, row in enumerate(reader, start=2):
            if row_number > self.MAX_IMPORT_ROWS + 1:
                raise UserError(
                    _("A single import is limited to %s opportunity rows.")
                    % self.MAX_IMPORT_ROWS
                )
            if None in row:
                raise UserError(
                    _("CSV row %s has more values than the header.") % row_number
                )
            if not any(str(value or "").strip() for value in row.values()):
                continue
            try:
                vals = self._row_values(row)
            except ValueError as exc:
                raise UserError(
                    _("CSV row %(row)s could not be imported: %(error)s",
                      row=row_number, error=exc)
                ) from exc
            identity = self._row_identity(vals)
            if identity in seen_rows:
                raise UserError(
                    _(
                        "CSV rows %(first)s and %(second)s identify the same "
                        "source listing.",
                        first=seen_rows[identity],
                        second=row_number,
                    )
                )
            seen_rows[identity] = row_number
            existing = self._find_existing_listing(Listing, vals)
            if existing and not self.update_existing:
                skipped += 1
                continue
            if self.validate_only:
                if existing:
                    updated += 1
                else:
                    created += 1
                continue
            try:
                if existing:
                    vals.pop("name", None)
                    vals = {
                        key: value
                        for key, value in vals.items()
                        if value not in (False, "", 0, 0.0)
                        or key in ("website_published", "public_status")
                    }
                    existing.write(vals)
                    imported |= existing
                    updated += 1
                else:
                    imported |= Listing.create(vals)
                    created += 1
            except (ValueError, ValidationError) as exc:
                raise UserError(_("CSV row %(row)s could not be imported: %(error)s", row=row_number, error=exc)) from exc

        if not created and not updated and not skipped:
            raise UserError(_("The CSV does not contain any opportunity rows."))
        summary = _(
            "%(mode)s complete: %(created)s new, %(updated)s updates, "
            "%(skipped)s skipped.",
            mode=_("Validation") if self.validate_only else _("Import"),
            created=created,
            updated=updated,
            skipped=skipped,
        )
        if self.validate_only:
            return {
                "type": "ir.actions.client",
                "tag": "display_notification",
                "params": {
                    "title": _("CSV Ready to Import"),
                    "message": summary,
                    "type": "success",
                    "sticky": True,
                },
            }
        if not imported:
            raise UserError(
                _("No listings were imported. %s existing rows were skipped.") % skipped
            )
        imported._recalculate_comp_analysis()
        next_action = {
            "type": "ir.actions.act_window",
            "name": _("Imported Sourced Listings"),
            "res_model": "southern.equipment.listing",
            "view_mode": "list,form",
            "views": [(False, "list"), (False, "form")],
            "domain": [("id", "in", imported.ids)],
            "context": {"search_default_needs_verification": 1},
        }
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Equipment Opportunities Imported"),
                "message": summary,
                "type": "success",
                "sticky": False,
                "next": next_action,
            },
        }


class SouthernEquipmentFacebookIntakeWizard(models.TransientModel):
    _name = "southern.equipment.facebook.intake.wizard"
    _description = "Queue a Facebook Marketplace Listing for Browser Enrichment"

    facebook_url = fields.Char(
        string="Facebook Marketplace or Share Link",
        required=True,
        help=(
            "Paste a visible Facebook Marketplace item link or Facebook share link. "
            "The signed-in browser agent will resolve and enrich the listing."
        ),
    )

    def _validated_url(self):
        self.ensure_one()
        value = (self.facebook_url or "").strip()
        try:
            parsed = urlsplit(value)
        except ValueError as exc:
            raise UserError(_("Enter a valid Facebook URL.")) from exc
        hostname = (parsed.hostname or "").lower()
        if (
            parsed.scheme.lower() not in ("http", "https")
            or hostname not in {"facebook.com", "www.facebook.com", "m.facebook.com"}
        ):
            raise UserError(
                _("Paste a facebook.com Marketplace item or share link.")
            )
        canonical = _canonical_source_url(value)
        if not re.match(
            r"^/(?:marketplace/item/\d+|share/(?:[A-Za-z]/)?[A-Za-z0-9_-]+)$",
            urlsplit(canonical).path,
        ):
            raise UserError(
                _("Use a Facebook Marketplace item URL or Facebook share URL.")
            )
        return canonical

    def action_queue_enrichment(self):
        self.ensure_one()
        canonical = self._validated_url()
        item_match = re.search(r"/marketplace/item/(\d+)", canonical)
        source_listing_id = item_match.group(1) if item_match else False
        Listing = self.env["southern.equipment.listing"]
        existing = Listing.browse()
        if source_listing_id:
            existing = Listing.search(
                [
                    ("source", "=", "facebook_marketplace"),
                    ("source_listing_id", "=", source_listing_id),
                ],
                limit=1,
            )
        if not existing:
            existing = Listing.search(
                [
                    ("source", "=", "facebook_marketplace"),
                    "|",
                    ("source_url", "=", canonical),
                    ("facebook_shared_url", "=", canonical),
                ],
                limit=1,
            )

        request_vals = {
            "facebook_shared_url": canonical,
            "facebook_intake_requested_by": self.env.user.id,
            "facebook_intake_requested_at": fields.Datetime.now(),
            "facebook_intake_error": False,
        }
        if existing:
            if existing.facebook_intake_status != "resolved":
                request_vals["facebook_intake_status"] = "pending"
            existing.write(request_vals)
            listing = existing
        else:
            pending_title = (
                _("Facebook Marketplace listing %(listing_id)s pending enrichment",
                  listing_id=source_listing_id)
                if source_listing_id
                else _("Facebook Marketplace share link pending enrichment")
            )
            listing = Listing.create(
                {
                    "company_id": self.env.company.id,
                    "broker_id": self.env.user.id,
                    "public_title": pending_title,
                    "public_status": "verification_in_progress",
                    "website_published": False,
                    "source": "facebook_marketplace",
                    "source_url": canonical,
                    "source_listing_id": source_listing_id,
                    "equipment_type": "other",
                    "verification_note": (
                        "Availability and listing details are pending visible-browser verification."
                    ),
                    "internal_notes": Markup(
                        "<p>Queued from the broker paste-link intake. "
                        "Do not publish until visible detail-page verification and "
                        "image-rights review are complete.</p>"
                    ),
                    **request_vals,
                    "facebook_intake_status": "pending",
                }
            )
        if listing.facebook_intake_status == "resolved":
            listing._recalculate_comp_analysis()
        return {
            "type": "ir.actions.act_window",
            "name": _("Facebook Listing Intake"),
            "res_model": "southern.equipment.listing",
            "res_id": listing.id,
            "view_mode": "form",
            "target": "current",
        }
