import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError


SOURCE_TYPES = [
    ("facebook_marketplace", "Facebook Marketplace"),
    ("machinerytrader", "MachineryTrader"),
    ("auctionvalues", "AuctionValues"),
    ("vip", "VIP"),
    ("dealer", "Dealer"),
    ("auction", "Auction"),
    ("manual", "Manual"),
    ("other", "Other"),
]


def _facebook_listing_id(value):
    match = re.fullmatch(
        r"https://(?:www\.)?facebook\.com/marketplace/item/(\d+)/?",
        (value or "").strip(),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else False


class SouthernEquipmentDiscoveryCandidate(models.Model):
    _name = "southern.equipment.discovery.candidate"
    _description = "Southern Equipment Discovery Candidate"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, discovered_at desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
    )
    source = fields.Selection(SOURCE_TYPES, required=True, default="manual", index=True)
    source_listing_id = fields.Char(index=True)
    canonical_url = fields.Char(required=True, index=True)
    exact_title = fields.Char(required=True)
    source_description = fields.Text()
    ask_price = fields.Monetary()
    approximate_location = fields.Char()
    discovered_at = fields.Datetime(default=fields.Datetime.now, required=True, index=True)
    discovered_by_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user, required=True
    )
    capture_run_id = fields.Char(index=True)
    priority = fields.Integer(default=10)
    state = fields.Selection(
        [
            ("new", "New"),
            ("needs_review", "Needs Broker Review"),
            ("verification", "Verification In Progress"),
            ("verified", "Verified"),
            ("rejected", "Rejected"),
            ("converted", "Converted to Listing"),
        ],
        default="new",
        required=True,
        tracking=True,
        index=True,
    )
    conflict_reason = fields.Text(tracking=True)
    evidence_ids = fields.One2many(
        "southern.equipment.discovery.evidence", "candidate_id"
    )
    listing_id = fields.Many2one(
        "southern.equipment.listing", readonly=True, ondelete="set null"
    )
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
    )
    manufacturer = fields.Char()
    model = fields.Char()
    year = fields.Integer()
    hours = fields.Float()
    title_confirmed = fields.Boolean()
    price_confirmed = fields.Boolean()
    location_confirmed = fields.Boolean()
    availability_confirmed = fields.Boolean()
    source_identity_confirmed = fields.Boolean()
    image_rights_confirmed = fields.Boolean()
    facts_not_inferred = fields.Boolean(
        help="Confirms that missing machine facts were not inferred or fabricated."
    )
    verification_ready = fields.Boolean(
        compute="_compute_verification_ready", store=True, index=True
    )
    verification_note = fields.Text()

    _source_listing_unique = models.Constraint(
        "unique(company_id, source, source_listing_id)",
        "This source listing already exists in the discovery queue.",
    )
    _canonical_url_unique = models.Constraint(
        "unique(company_id, canonical_url)",
        "This canonical source URL already exists in the discovery queue.",
    )

    @api.depends("exact_title", "source_listing_id")
    def _compute_name(self):
        for candidate in self:
            candidate.name = candidate.exact_title or candidate.source_listing_id or _("Candidate")

    @api.depends(
        "title_confirmed",
        "price_confirmed",
        "location_confirmed",
        "availability_confirmed",
        "source_identity_confirmed",
        "image_rights_confirmed",
        "facts_not_inferred",
        "conflict_reason",
    )
    def _compute_verification_ready(self):
        for candidate in self:
            candidate.verification_ready = bool(
                candidate.title_confirmed
                and candidate.price_confirmed
                and candidate.location_confirmed
                and candidate.availability_confirmed
                and candidate.source_identity_confirmed
                and candidate.image_rights_confirmed
                and candidate.facts_not_inferred
                and not candidate.conflict_reason
            )

    def action_start_verification(self):
        self.write({"state": "verification"})

    @api.model
    def ingest_candidate(self, values):
        values = dict(values or {})
        company_id = values.get("company_id") or self.env.company.id
        domain = [
            ("company_id", "=", company_id),
            ("canonical_url", "=", values.get("canonical_url")),
        ]
        if values.get("source_listing_id"):
            domain = [
                ("company_id", "=", company_id),
                ("source", "=", values.get("source", "manual")),
                ("source_listing_id", "=", values["source_listing_id"]),
            ]
        candidate = self.search(domain, limit=1)
        values["company_id"] = company_id
        if candidate:
            if candidate.state in ("verified", "converted", "rejected"):
                return candidate.id
            candidate.write(values)
            return candidate.id
        return self.create(values).id

    def action_needs_review(self):
        self.write({"state": "needs_review"})

    def action_verify(self):
        for candidate in self:
            if not candidate.verification_ready:
                raise UserError(
                    _("Complete the verification checklist and resolve conflicts first.")
                )
            if candidate.source == "facebook_marketplace":
                url_id = _facebook_listing_id(candidate.canonical_url)
                if not url_id or url_id != candidate.source_listing_id:
                    raise UserError(
                        _(
                            "The Facebook canonical URL and numeric source listing ID must match."
                        )
                    )
        self.write({"state": "verified"})

    def action_reject(self):
        self.write({"state": "rejected"})

    def action_convert_to_listing(self):
        Listing = self.env["southern.equipment.listing"]
        for candidate in self:
            if candidate.state != "verified" or not candidate.verification_ready:
                raise UserError(_("Only a verified candidate can become a sourced listing."))
            if candidate.listing_id:
                continue
            public_price = candidate.ask_price * 1.05 if candidate.ask_price else 0.0
            listing = Listing.create(
                {
                    "company_id": candidate.company_id.id,
                    "source": candidate.source,
                    "source_url": candidate.canonical_url,
                    "source_listing_id": candidate.source_listing_id,
                    "capture_run_id": candidate.capture_run_id,
                    "raw_capture_text": candidate.source_description,
                    "seller_ask_price": candidate.ask_price,
                    "seller_exact_location": candidate.approximate_location,
                    "public_title": candidate.exact_title,
                    "public_region": candidate.approximate_location,
                    "equipment_type": candidate.equipment_type,
                    "manufacturer": candidate.manufacturer,
                    "model": candidate.model,
                    "year": candidate.year,
                    "hours": candidate.hours,
                    "ask_price": candidate.ask_price,
                    "public_price": public_price,
                    "photo_rights_confirmed": candidate.image_rights_confirmed,
                    "verification_note": candidate.verification_note,
                    "public_status": "verification_in_progress",
                    "website_published": False,
                }
            )
            candidate.write({"state": "converted", "listing_id": listing.id})
        return True


class SouthernEquipmentDiscoveryEvidence(models.Model):
    _name = "southern.equipment.discovery.evidence"
    _description = "Southern Equipment Discovery Evidence"
    _order = "captured_at desc, id desc"

    candidate_id = fields.Many2one(
        "southern.equipment.discovery.candidate",
        required=True,
        ondelete="cascade",
        index=True,
    )
    evidence_type = fields.Selection(
        [
            ("detail_page", "Detail Page"),
            ("availability", "Availability"),
            ("identity", "Source Identity"),
            ("specification", "Specification"),
            ("price", "Price"),
            ("location", "Location"),
            ("image_rights", "Image Rights"),
            ("conflict", "Conflict"),
            ("other", "Other"),
        ],
        required=True,
        index=True,
    )
    source_url = fields.Char(required=True)
    observed_value = fields.Text(required=True)
    captured_at = fields.Datetime(default=fields.Datetime.now, required=True)
    captured_by_id = fields.Many2one(
        "res.users", default=lambda self: self.env.user, required=True
    )
    artifact_sha256 = fields.Char(index=True)
    attachment_ids = fields.Many2many("ir.attachment")
    note = fields.Text()
