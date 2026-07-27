from odoo import api, fields, models


BROKER_GROUPS = (
    "southern_equipment_brokerage.group_southern_equipment_admin,"
    "southern_equipment_brokerage.group_southern_deal_broker"
)

CONDITION_SELECTION = [
    ("unknown", "Not Documented"),
    ("excellent", "Excellent"),
    ("good", "Good"),
    ("average", "Average"),
    ("rough", "Rough"),
    ("inoperable", "Inoperable"),
    ("salvage", "Salvage"),
]


class SouthernEquipmentSpecProfile(models.Model):
    _name = "southern.equipment.spec.profile"
    _description = "Equipment Specification Profile"
    _order = "equipment_type, manufacturer, model"
    _rec_name = "name"

    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        readonly=True,
    )
    equipment_type = fields.Selection(
        selection=lambda self: self.env["southern.equipment.listing"]._fields[
            "equipment_type"
        ].selection,
        required=True,
        index=True,
    )
    manufacturer = fields.Char(required=True, index=True)
    model = fields.Char(required=True, index=True)
    name = fields.Char(compute="_compute_name", store=True)
    operating_weight_lb = fields.Float(string="Operating Weight (lb)")
    horsepower = fields.Float(string="Horsepower")
    rated_capacity_lb = fields.Float(string="Rated Capacity (lb)")
    lift_height_ft = fields.Float(string="Lift Height (ft)")
    undercarriage = fields.Selection(
        [
            ("tracked", "Tracked"),
            ("wheeled", "Wheeled"),
            ("other", "Other"),
        ]
    )
    configuration = fields.Char(
        help="Important configuration such as LGP, XL, high-flow, or long reach."
    )
    source_note = fields.Char(
        required=True,
        help="Document the manufacturer sheet, handbook, or other authorized source.",
    )
    source_url = fields.Char()
    notes = fields.Text()

    _profile_identity_unique = models.Constraint(
        "unique(company_id, equipment_type, manufacturer, model)",
        "A specification profile already exists for this company, class, make, and model.",
    )
    _profile_nonnegative = models.Constraint(
        """
        CHECK(
            operating_weight_lb >= 0
            AND horsepower >= 0
            AND rated_capacity_lb >= 0
            AND lift_height_ft >= 0
        )
        """,
        "Equipment specifications cannot be negative.",
    )

    @api.depends("manufacturer", "model", "equipment_type")
    def _compute_name(self):
        labels = dict(
            self.env["southern.equipment.listing"]._fields[
                "equipment_type"
            ].selection
        )
        for profile in self:
            profile.name = " ".join(
                part
                for part in (
                    profile.manufacturer,
                    profile.model,
                    labels.get(profile.equipment_type),
                )
                if part
            )


class SouthernEquipmentListingValuationGovernance(models.Model):
    _inherit = "southern.equipment.listing"

    spec_profile_id = fields.Many2one(
        "southern.equipment.spec.profile",
        string="Specification Profile",
        domain="[('company_id', '=', company_id), ('equipment_type', '=', equipment_type)]",
        groups=BROKER_GROUPS,
        help="The verified machine specification used for peer-model comparisons.",
    )
    condition_grade = fields.Selection(
        CONDITION_SELECTION,
        string="Documented Condition",
        default="unknown",
        groups=BROKER_GROUPS,
    )
    actual_sale_price = fields.Monetary(
        string="Actual Sale Price",
        groups=BROKER_GROUPS,
        help="Final realized transaction price used to back-test the valuation model.",
    )
    valuation_at_sale = fields.Monetary(
        string="Valuation at Sale",
        readonly=True,
        groups=BROKER_GROUPS,
    )
    valuation_error_pct = fields.Float(
        string="Valuation Error %",
        compute="_compute_valuation_accuracy",
        store=True,
        groups=BROKER_GROUPS,
    )
    valuation_accuracy = fields.Selection(
        [
            ("unavailable", "Unavailable"),
            ("within_10", "Within 10%"),
            ("within_20", "Within 20%"),
            ("outside_20", "Outside 20%"),
        ],
        compute="_compute_valuation_accuracy",
        store=True,
        groups=BROKER_GROUPS,
    )

    @api.depends("actual_sale_price", "valuation_at_sale")
    def _compute_valuation_accuracy(self):
        for listing in self:
            if not listing.actual_sale_price or not listing.valuation_at_sale:
                listing.valuation_error_pct = 0.0
                listing.valuation_accuracy = "unavailable"
                continue
            error = (
                (listing.valuation_at_sale - listing.actual_sale_price)
                / listing.actual_sale_price
                * 100.0
            )
            listing.valuation_error_pct = error
            absolute_error = abs(error)
            listing.valuation_accuracy = (
                "within_10"
                if absolute_error <= 10.0
                else "within_20"
                if absolute_error <= 20.0
                else "outside_20"
            )

    def write(self, values):
        if values.get("public_status") == "sold":
            for listing in self:
                row_values = dict(values)
                if not listing.valuation_at_sale and listing.comp_median:
                    row_values["valuation_at_sale"] = listing.comp_median
                super(
                    SouthernEquipmentListingValuationGovernance, listing
                ).write(row_values)
            return True
        return super().write(values)


class SouthernEquipmentCompValuationGovernance(models.Model):
    _inherit = "southern.equipment.comp"

    spec_profile_id = fields.Many2one(
        "southern.equipment.spec.profile",
        string="Specification Profile",
        domain="[('company_id', '=', company_id), ('equipment_type', '=', equipment_type)]",
        help="The verified machine specification used for peer-model comparisons.",
    )
    condition_grade = fields.Selection(
        CONDITION_SELECTION,
        string="Documented Condition",
        default="unknown",
    )


class SouthernEquipmentCompAuditLine(models.TransientModel):
    _name = "southern.equipment.comp.audit.line"
    _description = "Comparable Selection Audit"
    _order = "included desc, match_weight desc, comp_id desc"

    user_id = fields.Many2one(
        "res.users", required=True, default=lambda self: self.env.user, index=True
    )
    listing_id = fields.Many2one(
        "southern.equipment.listing", required=True, ondelete="cascade", index=True
    )
    comp_id = fields.Many2one(
        "southern.equipment.comp", required=True, ondelete="cascade", index=True
    )
    included = fields.Boolean(readonly=True)
    reason = fields.Char(readonly=True)
    match_basis = fields.Selection(
        [
            ("exact_model", "Exact Model"),
            ("same_make_family", "Same-Make Family"),
            ("spec_peer", "Specification Peer"),
            ("excluded", "Excluded"),
        ],
        readonly=True,
    )
    year_difference = fields.Integer(readonly=True)
    hour_difference = fields.Float(readonly=True)
    match_weight = fields.Float(readonly=True, digits=(16, 4))
    manufacturer = fields.Char(related="comp_id.manufacturer", readonly=True)
    model = fields.Char(related="comp_id.model", readonly=True)
    year = fields.Integer(related="comp_id.year", readonly=True)
    hours = fields.Float(related="comp_id.hours", readonly=True)
    price = fields.Monetary(related="comp_id.price", readonly=True)
    currency_id = fields.Many2one(related="comp_id.currency_id", readonly=True)
    condition_grade = fields.Selection(
        related="comp_id.condition_grade", readonly=True
    )
    sale_type = fields.Selection(related="comp_id.sale_type", readonly=True)
    source = fields.Char(related="comp_id.source", readonly=True)
