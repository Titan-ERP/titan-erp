from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class SouthernStripePayoutEvidence(models.Model):
    _name = "southern.stripe.payout.evidence"
    _description = "Southern Stripe Payout Evidence"
    _inherit = ["mail.thread", "mail.activity.mixin"]  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
    _order = "arrival_date desc, id desc"

    name = fields.Char(compute="_compute_name", store=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    run_id = fields.Many2one("southern.accounting.automation.run", ondelete="set null", index=True)
    stripe_payout_id = fields.Char(required=True, index=True, tracking=True, copy=False)
    status = fields.Char()
    arrival_date = fields.Date(index=True, tracking=True)
    created_date = fields.Date(index=True)
    currency_id = fields.Many2one(
        "res.currency", required=True, default=lambda self: self.env.company.currency_id
    )
    gross_charges = fields.Monetary(currency_field="currency_id", tracking=True)
    stripe_fees = fields.Monetary(currency_field="currency_id", tracking=True)
    processing_fee_charged = fields.Monetary(currency_field="currency_id")
    processing_fee_margin = fields.Monetary(
        currency_field="currency_id",
        compute="_compute_processing_fee_margin",
        store=True,
        help="Customer processing fees charged minus actual Stripe fees.",
    )
    refunds = fields.Monetary(currency_field="currency_id", tracking=True)
    disputes = fields.Monetary(currency_field="currency_id", tracking=True)
    adjustments = fields.Monetary(currency_field="currency_id", tracking=True)
    expected_net = fields.Monetary(currency_field="currency_id", tracking=True)
    stripe_payout_net = fields.Monetary(currency_field="currency_id", tracking=True)
    variance = fields.Monetary(currency_field="currency_id", tracking=True)
    transaction_count = fields.Integer()
    charge_count = fields.Integer()
    matched_bank_line_ids = fields.Many2many(
        "account.bank.statement.line",
        "southern_stripe_payout_bank_line_rel",
        "payout_id",
        "bank_line_id",
        string="Matched Bank Lines",
    )
    matched_bank_line_count = fields.Integer(compute="_compute_match_counts", store=True)
    stripe_clearing_move_ids = fields.Many2many(
        "account.move",
        "southern_stripe_payout_clearing_move_rel",
        "payout_id",
        "move_id",
        string="Stripe Clearing Moves",
    )
    stripe_bridge_move_ids = fields.Many2many(
        "account.move",
        "southern_stripe_payout_bridge_move_rel",
        "payout_id",
        "move_id",
        string="Bank Bridge Moves",
    )
    matched_terminal_payment_ids_text = fields.Char(
        string="Terminal Payment IDs",
        help="Comma-separated southern.stripe.terminal.payment IDs when that optional module is present.",
    )
    matched_payment_ids = fields.Many2many(
        "account.payment",
        "southern_stripe_payout_payment_rel",
        "payout_id",
        "payment_id",
        string="Odoo Payments",
    )
    linked_invoice_ids = fields.Many2many(
        "account.move",
        "southern_stripe_payout_invoice_rel",
        "payout_id",
        "move_id",
        string="Linked Invoices",
    )
    unmatched_payment_intents = fields.Text()
    artifact_uri = fields.Char()
    artifact_sha256 = fields.Char()
    artifact_schema_version = fields.Char(default="stripe-payout-observe-v1")
    state = fields.Selection(
        [
            ("observed", "Observed"),
            ("candidate", "Candidate"),
            ("matched", "Matched"),
            ("in_transit", "In Transit"),
            ("review_required", "Review Required"),
            ("blocked", "Blocked"),
            ("applied", "Applied"),
        ],
        default="observed",
        required=True,
        tracking=True,
        index=True,
    )
    reason_code = fields.Selection(
        [
            ("EXACT_PAYOUT_EVIDENCE", "Exact Payout Evidence"),
            ("STRIPE_CLEARING_BRIDGED", "Stripe Clearing Bridged"),
            ("STRIPE_CLEARING_IN_TRANSIT", "Stripe Clearing In Transit"),
            ("MISSING_BANK_LINE", "Missing Bank Line"),
            ("PAYOUT_VARIANCE", "Payout Variance"),
            ("UNLINKED_PAYMENT_INTENT", "Unlinked Payment Intent"),
            ("REFUND_PRESENT", "Refund Present"),
            ("DISPUTE_OR_CHARGEBACK", "Dispute Or Chargeback"),
            ("DUPLICATE_PAYOUT", "Duplicate Payout"),
            ("COMPANY_MISMATCH", "Company Mismatch"),
        ],
        index=True,
    )
    reason_note = fields.Text()

    _sql_constraints = [  # noqa: RUF012 - Odoo model declarations use mutable class attributes.
        (
            "southern_stripe_payout_company_unique",
            "unique(company_id, stripe_payout_id)",
            "Stripe payout evidence already exists for this company.",
        )
    ]

    @api.depends("stripe_payout_id", "arrival_date", "stripe_payout_net")
    def _compute_name(self):
        for payout in self:
            amount = payout.stripe_payout_net or 0.0
            payout.name = _("%(payout)s - %(date)s - %(amount).2f") % {
                "payout": payout.stripe_payout_id or _("Stripe Payout"),
                "date": payout.arrival_date or "",
                "amount": amount,
            }

    @api.depends("processing_fee_charged", "stripe_fees")
    def _compute_processing_fee_margin(self):
        for payout in self:
            payout.processing_fee_margin = payout.processing_fee_charged - payout.stripe_fees

    @api.depends("matched_bank_line_ids")
    def _compute_match_counts(self):
        for payout in self:
            payout.matched_bank_line_count = len(payout.matched_bank_line_ids)

    @api.constrains(
        "company_id",
        "matched_bank_line_ids",
        "stripe_clearing_move_ids",
        "stripe_bridge_move_ids",
        "matched_payment_ids",
        "linked_invoice_ids",
    )
    def _check_company_isolation(self):
        for payout in self:
            company = payout.company_id
            related_groups = (
                payout.matched_bank_line_ids,
                payout.stripe_clearing_move_ids,
                payout.stripe_bridge_move_ids,
                payout.matched_payment_ids,
                payout.linked_invoice_ids,
            )
            for records in related_groups:
                mismatched = records.filtered(
                    lambda record, expected_company=company: record.company_id
                    and record.company_id != expected_company
                )
                if mismatched:
                    raise ValidationError(
                        _("Stripe payout evidence cannot link records from another company.")
                    )

    @api.model
    def upsert_from_worker(self, values):
        payout_id = values.get("stripe_payout_id")
        company_id = values.get("company_id") or self.env.company.id
        if not payout_id:
            raise ValidationError(_("Missing Stripe payout ID."))
        existing = self.search(
            [("company_id", "=", company_id), ("stripe_payout_id", "=", payout_id)], limit=1
        )
        clean_values = dict(values, company_id=company_id)
        if existing:
            existing.write(clean_values)
            return existing.id
        return self.create(clean_values).id

    def action_mark_review_required(self):
        self.write({"state": "review_required"})

    def action_mark_matched(self):
        for payout in self:
            if payout.variance:
                raise ValidationError(_("Cannot mark a payout with variance as matched."))
        self.write({"state": "matched", "reason_code": "STRIPE_CLEARING_BRIDGED"})
