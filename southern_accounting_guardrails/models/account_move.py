import re

from odoo import api, fields, models


SHOP_BOSS_REF_RE = re.compile(r"\b(RO|PS|PO|WIP)\s*#?\s*(\d+)\b", re.I)


class AccountMove(models.Model):
    _inherit = "account.move"

    southern_source_system = fields.Selection(
        [
            ("odoo", "Odoo"),
            ("shop_boss", "Shop Boss"),
            ("bank_summary", "Bank Summary"),
            ("manual", "Manual"),
        ],
        string="Southern Source",
        default="odoo",
        index=True,
        copy=False,
    )
    southern_shop_boss_type = fields.Selection(
        [
            ("ro", "Repair Order"),
            ("ps", "Part Sale"),
            ("po", "Purchase Order"),
            ("wip", "Work in Progress"),
            ("other", "Other"),
        ],
        string="Shop Boss Type",
        index=True,
        copy=False,
    )
    southern_shop_boss_number = fields.Char(
        string="Shop Boss Number",
        index=True,
        copy=False,
    )
    southern_shop_boss_status = fields.Selection(
        [
            ("open", "Open"),
            ("wip", "WIP"),
            ("final", "Final"),
            ("closed", "Closed"),
            ("paid", "Paid"),
            ("unknown", "Unknown"),
        ],
        string="Shop Boss Status",
        copy=False,
    )
    southern_shop_boss_verified = fields.Boolean(
        string="Shop Boss Verified",
        copy=False,
        tracking=True,
    )
    southern_shop_boss_document_id = fields.Many2one(
        "southern.shop_boss.document",
        string="Shop Boss Document",
        copy=False,
        index=True,
        tracking=True,
    )
    southern_review_status = fields.Selection(
        [
            ("not_required", "Not Required"),
            ("needs_review", "Needs Review"),
            ("verified", "Verified"),
            ("exception", "Exception"),
        ],
        string="Southern Review",
        default="needs_review",
        index=True,
        copy=False,
        tracking=True,
    )
    southern_review_note = fields.Text(string="Southern Review Note", copy=False)
    southern_has_shop_boss_reference = fields.Boolean(
        string="Has Shop Boss Reference",
        compute="_compute_southern_has_shop_boss_reference",
        store=True,
        index=True,
    )
    southern_shop_boss_reference = fields.Char(
        string="Shop Boss Reference",
        compute="_compute_southern_shop_boss_reference",
        store=True,
        index=True,
    )

    @api.depends("ref", "invoice_origin", "narration", "southern_shop_boss_type", "southern_shop_boss_number")
    def _compute_southern_has_shop_boss_reference(self):
        for move in self:
            move.southern_has_shop_boss_reference = bool(
                move.southern_shop_boss_number or move._southern_extract_shop_boss_reference()
            )

    @api.depends("ref", "invoice_origin", "narration", "southern_shop_boss_type", "southern_shop_boss_number")
    def _compute_southern_shop_boss_reference(self):
        for move in self:
            if move.southern_shop_boss_type and move.southern_shop_boss_number:
                move.southern_shop_boss_reference = (
                    f"{move.southern_shop_boss_type.upper()} {move.southern_shop_boss_number}"
                )
                continue
            match = move._southern_extract_shop_boss_reference()
            move.southern_shop_boss_reference = f"{match[0].upper()} {match[1]}" if match else False

    def _southern_extract_shop_boss_reference(self):
        self.ensure_one()
        haystack = " ".join(
            str(value or "")
            for value in (self.ref, self.invoice_origin, self.narration, self.name)
        )
        match = SHOP_BOSS_REF_RE.search(haystack)
        if not match:
            return False
        return match.group(1), match.group(2)

    def action_southern_mark_shop_boss_verified(self):
        for move in self:
            vals = {
                "southern_source_system": "shop_boss",
                "southern_shop_boss_verified": True,
                "southern_review_status": "verified",
            }
            match = move._southern_extract_shop_boss_reference()
            if match and not move.southern_shop_boss_number:
                vals.update(
                    {
                        "southern_shop_boss_type": match[0].lower(),
                        "southern_shop_boss_number": match[1],
                    }
                )
            move.write(vals)
            if move.southern_shop_boss_document_id:
                move.southern_shop_boss_document_id.write(
                    {
                        "invoice_id": move.id,
                        "coverage_status": "invoice_linked",
                    }
                )

    def action_southern_mark_exception(self):
        self.write({"southern_review_status": "exception"})

    def action_southern_mark_needs_review(self):
        self.write({"southern_review_status": "needs_review"})
