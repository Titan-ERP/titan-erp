import base64
import csv
import io
import re

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.fields import Domain


MAX_IMPORT_BYTES = 5 * 1024 * 1024
MAX_IMPORT_ROWS = 10000
MATCH_CHUNK_SIZE = 100
CREATE_CHUNK_SIZE = 500


def _normal_email(value):
    return (value or "").strip().casefold()


def _normal_phone(value):
    digits = re.sub(r"\D+", "", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


def _normal_name(value):
    return " ".join(re.sub(r"[^a-z0-9]+", " ", (value or "").casefold()).split())


class SouthernContactImportBatch(models.Model):
    _name = "southern.contact.import.batch"
    _description = "Southern Contact Import Match Batch"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "create_date desc, id desc"

    name = fields.Char(required=True, default=lambda self: _("Contact Import Review"))
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    source_system = fields.Char(required=True)
    source_file = fields.Binary(required=True, attachment=True)
    source_filename = fields.Char()
    name_column = fields.Char(default="name", required=True)
    email_column = fields.Char(default="email", required=True)
    phone_column = fields.Char(default="phone", required=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("matched", "Matched"),
            ("reviewed", "Reviewed"),
            ("complete", "Complete"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        required=True,
        tracking=True,
        index=True,
    )
    line_ids = fields.One2many("southern.contact.import.line", "batch_id")
    line_count = fields.Integer(compute="_compute_counts")
    exact_match_count = fields.Integer(compute="_compute_counts")
    review_count = fields.Integer(compute="_compute_counts")
    new_count = fields.Integer(compute="_compute_counts")
    prepared_at = fields.Datetime(readonly=True)
    review_note = fields.Text(tracking=True)

    @api.depends("line_ids", "line_ids.decision")
    def _compute_counts(self):
        for batch in self:
            batch.line_count = len(batch.line_ids)
            batch.exact_match_count = len(
                batch.line_ids.filtered(lambda line: line.decision == "exact_match")
            )
            batch.review_count = len(
                batch.line_ids.filtered(lambda line: line.decision == "review")
            )
            batch.new_count = len(
                batch.line_ids.filtered(lambda line: line.decision == "new")
            )

    def action_prepare_matches(self):
        Partner = self.env["res.partner"].with_context(active_test=False)
        for batch in self:
            if not batch.source_file:
                raise UserError(_("Upload a CSV file first."))
            if batch.source_filename and not batch.source_filename.casefold().endswith(".csv"):
                raise UserError(_("Contact imports must use a .csv file."))
            try:
                raw = base64.b64decode(batch.source_file, validate=True)
                if len(raw) > MAX_IMPORT_BYTES:
                    raise UserError(_("The contact import exceeds the 5 MB safety limit."))
                payload = raw.decode("utf-8-sig")
                reader = csv.DictReader(io.StringIO(payload))
                required_columns = {
                    batch.name_column,
                    batch.email_column,
                    batch.phone_column,
                }
                if not reader.fieldnames or not required_columns.issubset(set(reader.fieldnames)):
                    raise UserError(_("The CSV does not contain the configured name, email, and phone columns."))
                rows = []
                for row_number, row in enumerate(reader, start=2):
                    if len(rows) >= MAX_IMPORT_ROWS:
                        raise UserError(_("The contact import exceeds the 10,000-row safety limit."))
                    rows.append(
                        {
                            "source_row": row_number,
                            "name": (row.get(batch.name_column) or "").strip(),
                            "email": _normal_email(row.get(batch.email_column)),
                            "phone": _normal_phone(row.get(batch.phone_column)),
                        }
                    )
            except (ValueError, UnicodeDecodeError, csv.Error) as error:
                raise UserError(_("The uploaded CSV could not be read: %s") % error) from error
            values = []
            for offset in range(0, len(rows), MATCH_CHUNK_SIZE):
                source_chunk = rows[offset : offset + MATCH_CHUNK_SIZE]
                candidate_domains = []
                for row in source_chunk:
                    row_domain = []
                    if row["email"]:
                        row_domain.append(Domain("email", "=ilike", row["email"]))
                    if row["phone"]:
                        phone_tail = row["phone"][-7:]
                        row_domain.extend(
                            [
                                Domain("phone", "ilike", phone_tail),
                                Domain("mobile", "ilike", phone_tail),
                            ]
                        )
                    if row["name"]:
                        row_domain.append(Domain("name", "=ilike", row["name"]))
                    if row_domain:
                        candidate_domains.append(Domain.OR(row_domain))
                candidate_domain = (
                    Domain.OR(candidate_domains)
                    if candidate_domains
                    else Domain.FALSE
                )
                company_domain = Domain(
                    "company_id",
                    "in",
                    [False, batch.company_id.id],
                )
                partners = Partner.search(
                    company_domain & candidate_domain,
                    limit=2000,
                )
                by_email = {}
                by_phone = {}
                by_name = {}
                for partner in partners:
                    email = _normal_email(partner.email)
                    phones = {
                        _normal_phone(partner.phone),
                        _normal_phone(partner.mobile),
                    } - {""}
                    name = _normal_name(partner.name)
                    if email:
                        by_email.setdefault(email, set()).add(partner)
                    for phone in phones:
                        by_phone.setdefault(phone, set()).add(partner)
                    if name:
                        by_name.setdefault(name, set()).add(partner)
                for row in source_chunk:
                    email_matches = set(by_email.get(row["email"], [])) if row["email"] else set()
                    phone_matches = set(by_phone.get(row["phone"], [])) if row["phone"] else set()
                    name_matches = set(by_name.get(_normal_name(row["name"]), [])) if row["name"] else set()
                    identity_conflict = False
                    if row["email"] and row["phone"]:
                        candidates = email_matches & phone_matches
                        identity_conflict = not candidates and bool(email_matches or phone_matches)
                    elif row["email"]:
                        candidates = email_matches
                    elif row["phone"]:
                        candidates = phone_matches
                    else:
                        candidates = set()
                    if len(candidates) == 1:
                        partner = candidates.pop()
                        decision = "exact_match"
                        reason = _("Unique exact email/phone match")
                    elif len(candidates) > 1:
                        partner = False
                        decision = "review"
                        reason = _("Multiple contacts share the supplied identity")
                    elif identity_conflict:
                        partner = False
                        decision = "review"
                        reason = _("Email and phone point to conflicting existing contacts")
                    elif name_matches:
                        partner = False
                        decision = "review"
                        reason = _("Name matches an existing contact; review before creating")
                    else:
                        partner = False
                        decision = "new"
                        reason = _("No exact email or phone match")
                    values.append(
                        {
                            "batch_id": batch.id,
                            "source_row": row["source_row"],
                            "source_name": row["name"],
                            "source_email": row["email"],
                            "source_phone": row["phone"],
                            "decision": decision,
                            "partner_id": partner.id if partner else False,
                            "match_reason": reason,
                        }
                    )
            batch.line_ids.unlink()
            Line = self.env["southern.contact.import.line"]
            for offset in range(0, len(values), CREATE_CHUNK_SIZE):
                Line.create(values[offset : offset + CREATE_CHUNK_SIZE])
            batch.write({"state": "matched", "prepared_at": fields.Datetime.now()})
        return True

    def action_mark_reviewed(self):
        self.write({"state": "reviewed"})

    def action_complete(self):
        if self.filtered(lambda batch: batch.line_ids.filtered(lambda line: line.decision == "review")):
            raise UserError(_("Resolve all ambiguous match rows before completing the batch."))
        self.write({"state": "complete"})


class SouthernContactImportLine(models.Model):
    _name = "southern.contact.import.line"
    _description = "Southern Contact Import Match Line"
    _order = "batch_id, source_row, id"

    batch_id = fields.Many2one(
        "southern.contact.import.batch", required=True, ondelete="cascade", index=True
    )
    source_row = fields.Integer(required=True)
    source_name = fields.Char()
    source_email = fields.Char(index=True)
    source_phone = fields.Char(index=True)
    decision = fields.Selection(
        [
            ("exact_match", "Exact Match"),
            ("review", "Needs Review"),
            ("new", "New Contact Candidate"),
            ("skip", "Skip"),
        ],
        required=True,
        index=True,
    )
    partner_id = fields.Many2one("res.partner", index=True)
    match_reason = fields.Char()
    reviewer_note = fields.Char()

    _batch_source_row_unique = models.Constraint(
        "unique(batch_id, source_row)",
        "Each source row can appear only once in an import batch.",
    )
