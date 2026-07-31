from odoo import _, api, fields, models


class SouthernShopBossDocument(models.Model):
    _name = "southern.shop_boss.document"
    _description = "Shop Boss Accounting Document"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "document_date desc, document_type, document_number"
    _rec_name = "name"

    name = fields.Char(compute="_compute_name", store=True, index=True)
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
    document_type = fields.Selection(
        [
            ("ro", "Repair Order"),
            ("ps", "Part Sale"),
            ("po", "Purchase Order"),
            ("wip", "Work in Progress"),
            ("other", "Other"),
        ],
        required=True,
        index=True,
        tracking=True,
    )
    document_number = fields.Char(required=True, index=True, tracking=True)
    document_date = fields.Date(required=True, index=True, tracking=True)
    shop_boss_status = fields.Selection(
        [
            ("open", "Open"),
            ("wip", "WIP"),
            ("final", "Final"),
            ("closed", "Closed"),
            ("paid", "Paid"),
            ("unknown", "Unknown"),
        ],
        default="unknown",
        index=True,
        tracking=True,
    )
    source_file = fields.Char()
    source_row_hash = fields.Char(index=True, copy=False)

    customer_name = fields.Char(index=True, tracking=True)
    partner_id = fields.Many2one("res.partner", string="Odoo Customer/Vendor", index=True, tracking=True)
    vehicle_or_equipment = fields.Char()
    source_notes = fields.Text()

    parts_amount = fields.Monetary(default=0.0, tracking=True)
    service_amount = fields.Monetary(default=0.0, tracking=True)
    rental_amount = fields.Monetary(default=0.0, tracking=True)
    equipment_amount = fields.Monetary(default=0.0, tracking=True)
    fees_amount = fields.Monetary(default=0.0, tracking=True)
    discount_amount = fields.Monetary(default=0.0, tracking=True)
    tax_amount = fields.Monetary(default=0.0, tracking=True)
    total_amount = fields.Monetary(compute="_compute_totals", store=True)
    revenue_amount = fields.Monetary(compute="_compute_totals", store=True)

    invoice_id = fields.Many2one(
        "account.move",
        string="Odoo Invoice/Credit",
        domain="[('move_type', 'in', ['out_invoice', 'out_refund', 'in_invoice', 'in_refund'])]",
        tracking=True,
    )
    bank_statement_line_ids = fields.One2many(
        "account.bank.statement.line",
        "southern_shop_boss_document_id",
        string="Bank Lines",
    )
    payment_batch_id = fields.Many2one(
        "southern.shop_boss.payment.batch",
        string="Payment Batch",
        tracking=True,
    )
    coverage_status = fields.Selection(
        [
            ("not_reviewed", "Not Reviewed"),
            ("needs_invoice", "Needs Invoice"),
            ("invoice_linked", "Invoice Linked"),
            ("summary_revenue", "Covered By Summary Revenue"),
            ("bank_matched", "Bank Matched"),
            ("asset_sale", "Asset Sale / Loan Paydown"),
            ("exception", "Exception"),
        ],
        default="not_reviewed",
        index=True,
        tracking=True,
    )
    accounting_bucket = fields.Selection(
        [
            ("parts", "Parts"),
            ("service", "Service"),
            ("rental", "Rental"),
            ("equipment", "Equipment / Asset Sale"),
            ("mixed", "Mixed"),
            ("other", "Other"),
        ],
        compute="_compute_accounting_bucket",
        store=True,
        index=True,
    )
    review_note = fields.Text(tracking=True)

    _sql_constraints = [
        (
            "southern_shop_boss_doc_unique",
            "unique(company_id, document_type, document_number)",
            "A Shop Boss document with this type and number already exists for this company.",
        )
    ]

    @api.depends("document_type", "document_number")
    def _compute_name(self):
        labels = dict(self._fields["document_type"].selection)
        for document in self:
            prefix = labels.get(document.document_type, "Shop Boss")
            number = document.document_number or ""
            document.name = f"{prefix} {number}".strip()

    @api.depends(
        "parts_amount",
        "service_amount",
        "rental_amount",
        "equipment_amount",
        "fees_amount",
        "discount_amount",
        "tax_amount",
    )
    def _compute_totals(self):
        for document in self:
            revenue = (
                document.parts_amount
                + document.service_amount
                + document.rental_amount
                + document.equipment_amount
                + document.fees_amount
                - document.discount_amount
            )
            document.revenue_amount = revenue
            document.total_amount = revenue + document.tax_amount

    @api.depends("parts_amount", "service_amount", "rental_amount", "equipment_amount", "fees_amount")
    def _compute_accounting_bucket(self):
        for document in self:
            buckets = [
                bucket
                for bucket, amount in (
                    ("parts", document.parts_amount),
                    ("service", document.service_amount + document.fees_amount),
                    ("rental", document.rental_amount),
                    ("equipment", document.equipment_amount),
                )
                if amount
            ]
            if len(buckets) == 1:
                document.accounting_bucket = buckets[0]
            elif len(buckets) > 1:
                document.accounting_bucket = "mixed"
            else:
                document.accounting_bucket = "other"

    def action_mark_needs_invoice(self):
        self.write({"coverage_status": "needs_invoice"})

    def action_mark_summary_revenue(self):
        self.write({"coverage_status": "summary_revenue"})

    def action_mark_asset_sale(self):
        self.write({"coverage_status": "asset_sale"})

    def action_mark_exception(self):
        self.write({"coverage_status": "exception"})

    def action_find_partner(self):
        Partner = self.env["res.partner"].sudo()
        for document in self:
            if document.partner_id or not document.customer_name:
                continue
            partner = Partner.search([("name", "=", document.customer_name)], limit=1)
            if not partner:
                partner = Partner.search([("name", "ilike", document.customer_name)], limit=1)
            if partner:
                document.partner_id = partner.id

    def action_view_invoice(self):
        self.ensure_one()
        if not self.invoice_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Shop Boss Invoice"),
            "res_model": "account.move",
            "res_id": self.invoice_id.id,
            "view_mode": "form",
        }

    def action_view_bank_lines(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Shop Boss Bank Lines"),
            "res_model": "account.bank.statement.line",
            "view_mode": "list,form",
            "domain": [("southern_shop_boss_document_id", "=", self.id)],
        }
