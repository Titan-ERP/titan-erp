from odoo import _, api, fields, models, Command
from odoo.exceptions import UserError


class DmcCompanySetupWizard(models.TransientModel):
    _name = "dmc.company.setup.wizard"
    _description = "DMC New Company Setup Wizard"

    # ── State ────────────────────────────────────────────────────────────────

    state = fields.Selection(
        [
            ("company_info", "Company Information"),
            ("bank_setup", "Bank Account Setup"),
            ("journal_prefixes", "Journal Prefixes"),
            ("review", "Review & Confirm"),
            ("done", "Done"),
        ],
        default="company_info",
        required=True,
        string="Step",
    )

    # ── Step 1: Company Information ──────────────────────────────────────────

    company_name = fields.Char("Company Name", required=True)
    street = fields.Char("Street")
    street2 = fields.Char("Street 2")
    city = fields.Char("City")
    state_id = fields.Many2one("res.country.state", "State")
    zip = fields.Char("ZIP")
    country_id = fields.Many2one(
        "res.country",
        "Country",
        default=lambda self: self.env.ref("base.us", raise_if_not_found=False),
    )
    phone = fields.Char("Phone")
    email = fields.Char("Email")
    website = fields.Char("Website")
    vat = fields.Char("Tax ID")
    currency_id = fields.Many2one(
        "res.currency",
        "Currency",
        default=lambda self: self.env.ref("base.USD", raise_if_not_found=False),
    )
    logo = fields.Binary("Logo")
    parent_id = fields.Many2one("res.company", "Parent Company")
    tax_source_company_id = fields.Many2one(
        "res.company",
        "Copy Taxes & Payment Setup From",
    )

    # ── Step 2: Bank Account Setup ───────────────────────────────────────────

    bank_account_name = fields.Char("Bank Account Name")
    bank_account_code = fields.Char(
        "Bank Account Code",
        default=lambda self: self._next_bank_code(),
        help="Auto-generated from the highest existing Bank/Cash account code. Override if needed.",
    )
    bank_name = fields.Char("Bank Name", help="Optional: name of the physical bank")

    # ── Step 3: Journal Prefixes ─────────────────────────────────────────────

    journal_sales_prefix = fields.Char("Sales Prefix", default="INV")
    journal_purchase_prefix = fields.Char("Purchase Prefix", default="BILL")
    journal_bank_prefix = fields.Char("Bank Prefix", default="BNK")
    journal_misc_prefix = fields.Char("Misc Prefix", default="MISC")

    # ── Step 5: Result ───────────────────────────────────────────────────────

    created_company_id = fields.Many2one("res.company", "Created Company", readonly=True)
    created_journal_ids = fields.Many2many(
        "account.journal", string="Created Journals", readonly=True
    )
    result_message = fields.Text("Result", readonly=True)

    # ── Code auto-generation helpers ─────────────────────────────────────────

    def _next_bank_code(self):
        """Return the next available sequential Bank/Cash account code.

        Sequences from the highest existing asset_cash account code (so the
        suggestion stays in the same numeric range as other bank accounts).
        Then increments until the candidate is not in use by ANY account type,
        preventing collisions with non-bank accounts that share the range.
        Falls back to '101001' if no Bank/Cash accounts exist yet.

        Reads from BOTH the Odoo 17+ per-company mapping table AND the
        company_dependent code field, because accounts created outside the wizard
        (or before the mapping table existed) may only have the direct field set.
        """
        mapping_field, _ = self._find_account_code_mapping_field()
        all_accounts = self.env["account.account"].sudo().search([])

        def _collect_codes(accounts):
            codes = set()
            for acc in accounts:
                if mapping_field:
                    for mapping in acc[mapping_field]:
                        if mapping.code:
                            codes.add(mapping.code.strip())
                for company in acc.company_ids:
                    try:
                        code = acc.with_company(company).code
                        if code:
                            codes.add(code.strip())
                    except Exception:
                        pass
            return codes

        bank_accounts = all_accounts.filtered(
            lambda a: a.account_type == "asset_cash"
        )
        bank_codes = _collect_codes(bank_accounts)
        all_codes = _collect_codes(all_accounts)

        numeric_bank_codes = []
        for code in bank_codes:
            try:
                numeric_bank_codes.append(int(code))
            except (ValueError, TypeError):
                pass

        candidate = (max(numeric_bank_codes) + 1) if numeric_bank_codes else 101001
        while str(candidate) in all_codes:
            candidate += 1
        return str(candidate)

    @api.onchange("company_name")
    def _onchange_company_name(self):
        """Auto-fill the bank account name from the company name."""
        if self.company_name and not self.bank_account_name:
            self.bank_account_name = f"Bank {self.company_name}"

    # ── Navigation ───────────────────────────────────────────────────────────

    _NEXT_STATE = {
        "company_info": "bank_setup",
        "bank_setup": "journal_prefixes",
        "journal_prefixes": "review",
    }
    _PREV_STATE = {v: k for k, v in _NEXT_STATE.items()}
    _PREV_STATE["done"] = "review"

    def action_next(self):
        self.ensure_one()
        if self.state not in self._NEXT_STATE:
            return
        self._validate_current_step()
        self.state = self._NEXT_STATE[self.state]
        return self._reopen_wizard()

    def action_back(self):
        self.ensure_one()
        if self.state in self._PREV_STATE:
            self.state = self._PREV_STATE[self.state]
        return self._reopen_wizard()

    def _reopen_wizard(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
            "context": {"dialog_size": "large"},
        }

    # ── Per-step validation ──────────────────────────────────────────────────

    def _validate_current_step(self):
        self.ensure_one()
        if self.state == "company_info":
            if not self.company_name:
                raise UserError(_("Company Name is required."))
        elif self.state == "bank_setup":
            if not self.bank_account_code:
                raise UserError(_("Bank Account Code is required."))
            existing = self.env["account.account"].sudo().search(
                [("code", "=", self.bank_account_code)], limit=1
            )
            if existing:
                raise UserError(
                    _("Account code %s already exists. Please choose a unique code.")
                    % self.bank_account_code
                )
        elif self.state == "journal_prefixes":
            if not all(
                [
                    self.journal_sales_prefix,
                    self.journal_purchase_prefix,
                    self.journal_bank_prefix,
                    self.journal_misc_prefix,
                ]
            ):
                raise UserError(_("All journal prefixes are required."))

    # ── Main execution ───────────────────────────────────────────────────────

    def action_create_company(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                company = self._step1_create_company()
                bank_account = self._step2_create_bank_cash_accounts(company)
                self._step2b_map_new_bank_to_existing(company, bank_account, self.bank_account_code)
                self._step3_associate_shared_accounts(company)
                self._step4_copy_taxes_and_groups(company)
                self._step5_create_journals(company, bank_account)
                self._step6_create_payment_providers(company)
            self.sudo().created_company_id = company
            self.sudo().result_message = company.name
            self.sudo().state = "done"
        except UserError:
            raise
        except Exception as e:
            raise UserError(
                _("Company setup failed and has been fully rolled back.\n\nError: %s") % str(e)
            ) from e
        return self._reopen_wizard()

    # ── Step implementations ─────────────────────────────────────────────────

    def _step1_create_company(self):
        # Omit country_id during create so Odoo does not auto-apply the
        # country's default chart-of-accounts template (which would create
        # account.account records that conflict with Step 3's shared-account
        # mapping). We write country_id back onto the company immediately after.
        vals = {
            "name": self.company_name,
            "currency_id": self.currency_id.id if self.currency_id else False,
        }
        for f in ("street", "street2", "city", "zip", "phone", "email", "website", "vat"):
            val = getattr(self, f)
            if val:
                vals[f] = val
        if self.state_id:
            vals["state_id"] = self.state_id.id
        if self.parent_id:
            vals["parent_id"] = self.parent_id.id
        if self.logo:
            vals["logo"] = self.logo
        company = self.env["res.company"].sudo().create(vals)
        # Restore country after creation to avoid triggering chart template.
        if self.country_id:
            company.sudo().write({"country_id": self.country_id.id})
        return company

    def _step2_create_bank_cash_accounts(self, company):
        bank_name = self.bank_account_name or f"Bank {company.name}"
        # Create without with_company — a freshly created company is not fully
        # initialised in its own context yet and causes silent failures in Odoo 19.
        account = self.env["account.account"].sudo().create({
            "name": bank_name,
            "code": self.bank_account_code,
            "account_type": "asset_cash",
            "company_ids": [Command.set([company.id])],
        })
        # Explicitly populate the per-company mapping table (Odoo 17+/19).
        # The create() above stores the code in the calling env's company context,
        # so we write the mapping entry for the new company separately.
        mapping_field, _ = self._find_account_code_mapping_field()
        if mapping_field:
            already = account[mapping_field].filtered(
                lambda m, c=company: m.company_id.id == c.id
            )
            if not already:
                account.sudo().write({
                    mapping_field: [Command.create({
                        "company_id": company.id,
                        "code": self.bank_account_code,
                    })],
                })
        return account

    def _step2b_map_new_bank_to_existing(self, company, new_bank_account, bank_code):
        """Bidirectional mapping between the new bank account and all existing ones.

        Direction 1 — existing → new company:
            Each existing Bank/Cash account gets a mapping entry for the new
            company (code = bank_code), without adding it to company_ids.

        Direction 2 — new bank account → existing companies:
            The newly created bank account gets mapping entries for every company
            already mapped in existing bank accounts, without adding those
            companies to the new account's company_ids.

        Skips silently when bank_code is empty or the mapping table is absent.
        """
        mapping_field, _ = self._find_account_code_mapping_field()
        if not mapping_field or not bank_code:
            return

        existing_banks = self.env["account.account"].sudo().search([
            ("account_type", "=", "asset_cash"),
            ("company_ids", "!=", False),
            ("company_ids", "not in", [company.id]),
            ("id", "!=", new_bank_account.id),
        ])

        # Track companies already mapped in the new bank account to avoid duplicates.
        mapped_in_new = {m.company_id.id for m in new_bank_account[mapping_field]}

        for bank_acc in existing_banks:
            # Resolve the existing bank's own code from its primary company mapping.
            source_company = bank_acc.company_ids[:1]
            source_entry = bank_acc[mapping_field].filtered(
                lambda m, sc=source_company: m.company_id.id == sc.id
            )[:1]
            existing_bank_code = source_entry.code if source_entry else ""
            if not existing_bank_code and source_company:
                existing_bank_code = bank_acc.with_company(source_company).code or ""
            if not existing_bank_code:
                continue

            # Direction 1: map the new company into this existing bank using the
            # existing bank's own code — same pattern as shared accounts where
            # every company entry maps to the account's single canonical code.
            existing_entry = bank_acc[mapping_field].filtered(
                lambda m, c=company: m.company_id.id == c.id
            )
            if existing_entry:
                if not existing_entry.code:
                    existing_entry.sudo().write({"code": existing_bank_code})
            else:
                bank_acc.sudo().write({
                    mapping_field: [Command.create({
                        "company_id": company.id,
                        "code": existing_bank_code,
                    })],
                })

            # Direction 2: map each company that owns this existing bank into the
            # new bank account using the new bank's own code — same pattern,
            # every company maps to 'bank_code' in the new bank account's mapping.
            for existing_company in bank_acc.company_ids:
                if existing_company.id == company.id:
                    continue
                if existing_company.id in mapped_in_new:
                    continue
                new_bank_account.sudo().write({
                    mapping_field: [Command.create({
                        "company_id": existing_company.id,
                        "code": bank_code,
                    })],
                })
                mapped_in_new.add(existing_company.id)

    def _find_account_code_mapping_field(self):
        """
        Detect the One2many field on account.account that stores per-company code
        mappings (the field rendered in the Mapping tab of the account form).
        Returns (field_name, comodel_name) if found, or (None, None) otherwise.
        In Odoo 17+, this is a One2many to a model with company_id + code fields.
        """
        account_fields = self.env["account.account"]._fields
        for fname, field in account_fields.items():
            if field.type != "one2many":
                continue
            comodel = self.env.get(field.comodel_name)
            if comodel is None:
                continue
            cm_fields = comodel._fields
            if "company_id" not in cm_fields or "code" not in cm_fields:
                continue
            # Confirm the comodel has a Many2one back to account.account
            for cf in cm_fields.values():
                if cf.type == "many2one" and cf.comodel_name == "account.account":
                    return fname, field.comodel_name
        return None, None

    def _codes_owned_by_company(self, company, mapping_field):
        """Return the set of account codes already claimed by *company*.

        Odoo may auto-create accounts when a company is created (chart-template
        loading). We need to know these codes upfront so Step 3 can skip any
        shared account whose code would collide with an already-existing one.
        """
        codes = set()
        existing = self.env["account.account"].sudo().search(
            [("company_ids", "in", [company.id])]
        )
        for acc in existing:
            if mapping_field:
                for m in acc[mapping_field]:
                    if m.company_id.id == company.id and m.code:
                        codes.add(m.code)
            else:
                code = acc.with_company(company).code
                if code:
                    codes.add(code)
        return codes

    def _step3_associate_shared_accounts(self, company):
        # When a parent company is set, Odoo automatically inherits the parent's
        # Chart of Accounts — the child is added to all parent accounts' company_ids.
        # Running our mapping logic on top of that causes duplicate code errors.
        if company.parent_id:
            return

        # Only map non-Bank/Cash accounts that are already part of the shared chart.
        shared_accounts = self.env["account.account"].sudo().search(
            [
                ("account_type", "!=", "asset_cash"),
                ("company_ids", "!=", False),
            ]
        )
        if not shared_accounts:
            return

        mapping_field, _ = self._find_account_code_mapping_field()

        # Pre-compute codes already owned by the new company.
        # If Odoo auto-created accounts from a chart template during Step 1,
        # those codes are already taken; attempting to also link the shared
        # account with the same code would raise "Account codes must be unique".
        taken_codes = self._codes_owned_by_company(company, mapping_field)

        for account in shared_accounts:
            # Skip if the company is already linked to this account.
            if company in account.company_ids:
                continue

            source_company = account.company_ids[:1]
            existing_code = ""

            if mapping_field and source_company:
                mapping_rec = account[mapping_field].filtered(
                    lambda m: m.company_id.id == source_company.id
                )[:1]
                existing_code = mapping_rec.code if mapping_rec else ""
            elif source_company:
                existing_code = account.with_company(source_company).code or ""

            if not existing_code:
                continue

            # Skip if this code is already taken by an auto-created account.
            if existing_code in taken_codes:
                continue

            if mapping_field:
                # Both company_ids and the code mapping must be written in one
                # call so the constraint sees both changes simultaneously.
                account.write({
                    "company_ids": [Command.link(company.id)],
                    mapping_field: [Command.create({
                        "company_id": company.id,
                        "code": existing_code,
                    })],
                })
            else:
                # company_dependent fallback: set code first, then link.
                account.with_company(company).write({"code": existing_code})
                account.write({"company_ids": [Command.link(company.id)]})

            taken_codes.add(existing_code)

    def _company_abbrev(self, company):
        """Return uppercase initials of the company name. e.g. 'Sample Company' → 'SC'."""
        return "".join(w[0].upper() for w in company.name.split() if w)

    def _rep_line_vals(self, lines):
        """Build repartition line create-commands without account_id.

        Passing these explicitly in copy() default overrides the auto-copied
        lines that would carry account_id values from the source company.
        """
        result = []
        for line in lines:
            vals = {
                "factor_percent": line.factor_percent,
                "repartition_type": line.repartition_type,
            }
            if line.tag_ids:
                vals["tag_ids"] = [Command.set(line.tag_ids.ids)]
            result.append(Command.create(vals))
        return result

    def _step4_copy_taxes_and_groups(self, company):
        source = self.tax_source_company_id
        if not source:
            return

        existing_group_names = set(
            self.env["account.tax.group"].sudo()
            .search([("company_id", "=", company.id)])
            .mapped("name")
        )

        group_map = {}
        source_groups = self.env["account.tax.group"].sudo().search(
            [("company_id", "=", source.id)]
        )
        for old_group in source_groups:
            if old_group.name in existing_group_names:
                existing = self.env["account.tax.group"].sudo().search(
                    [("company_id", "=", company.id), ("name", "=", old_group.name)],
                    limit=1,
                )
                if existing:
                    group_map[old_group.id] = existing.id
                continue
            # Clear the payable/receivable account references to avoid Odoo's
            # cross-company constraint: the new tax group belongs to the new company
            # but those accounts' primary company_id is still the source company.
            new_group = old_group.sudo().copy({
                "company_id": company.id,
                "tax_payable_account_id": False,
                "tax_receivable_account_id": False,
                "advance_tax_payment_account_id": False,
            })
            group_map[old_group.id] = new_group.id

        # Include the parent company's taxes in the conflict check.
        # When parent_id is set, Odoo's uniqueness constraint covers taxes
        # visible across the company hierarchy, not just company_id = new_company.
        company_ids_to_check = [company.id]
        if company.parent_id:
            company_ids_to_check.append(company.parent_id.id)
        existing_tax_names = set(
            self.env["account.tax"].sudo()
            .search([("company_id", "in", company_ids_to_check)])
            .mapped("name")
        )

        source_taxes = self.env["account.tax"].sudo().search(
            [("company_id", "=", source.id)]
        )
        for old_tax in source_taxes:
            tax_name = old_tax.name
            if tax_name in existing_tax_names:
                tax_name = f"{old_tax.name} ({self._company_abbrev(company)})"
            existing_tax_names.add(tax_name)
            copy_vals = {
                "company_id": company.id,
                "name": tax_name,
                # Supply repartition lines without account_id. Passing them
                # explicitly in the copy() default dict replaces what copy_data()
                # would auto-generate. Odoo's _check_company constraint compares
                # account.company_id (computed = first in company_ids, still the
                # source company) against the new tax's company_id, so any
                # account_id reference causes a cross-company error regardless
                # of whether the account was shared in Step 3.
                "invoice_repartition_line_ids": self._rep_line_vals(
                    old_tax.invoice_repartition_line_ids
                ),
                "refund_repartition_line_ids": self._rep_line_vals(
                    old_tax.refund_repartition_line_ids
                ),
            }
            if old_tax.tax_group_id and old_tax.tax_group_id.id in group_map:
                copy_vals["tax_group_id"] = group_map[old_tax.tax_group_id.id]
            old_tax.sudo().copy(copy_vals)

    def _step5_create_journals(self, company, bank_account=None):
        Journal = self.env["account.journal"].sudo().with_company(company)

        journal_defs = [
            {"name": "Customer Invoices", "type": "sale", "code": self.journal_sales_prefix},
            {"name": "Vendor Bills", "type": "purchase", "code": self.journal_purchase_prefix},
            {
                "name": f"Bank {company.name}",
                "type": "bank",
                "code": self.journal_bank_prefix,
                **({"default_account_id": bank_account.id} if bank_account else {}),
            },
            {"name": "Miscellaneous Operations", "type": "general", "code": self.journal_misc_prefix},
        ]

        created = self.env["account.journal"].sudo()
        for vals in journal_defs:
            vals["company_id"] = company.id
            created |= Journal.create(vals)

        self.sudo().created_journal_ids = [Command.set(created.ids)]

    # In Odoo 19, payment.provider.code is a Selection with only these valid values:
    #   'none'   — No Provider Set
    #   'custom' — Custom  (covers both Wire Transfer AND Cash on Delivery)
    #   'demo'   — Demo
    # Wire Transfer and Cash on Delivery share code='custom'; they differ by name only.
    _DEFAULT_PROVIDERS = [
        ("custom", "Wire Transfer"),
        ("custom", "Cash on Delivery"),
        ("demo", "Demo"),
    ]

    def _step6_create_payment_providers(self, company):
        abbrev = self._company_abbrev(company)
        source = self.tax_source_company_id

        for code, base_name in self._DEFAULT_PROVIDERS:
            # Wire Transfer and Cash on Delivery both use code='custom'.
            # Search by exact name to tell them apart.
            template = self.env["payment.provider"].sudo().browse()
            if source:
                template = self.env["payment.provider"].sudo().search(
                    [("code", "=", code), ("company_id", "=", source.id),
                     ("name", "ilike", base_name)], limit=1
                )
            if not template:
                template = self.env["payment.provider"].sudo().search(
                    [("code", "=", code), ("name", "ilike", base_name)], limit=1
                )

            if template:
                copy_vals = {
                    "company_id": company.id,
                    "name": f"{base_name} {abbrev}",
                }
                # custom_mode has copy=False in Odoo 19 but is required for
                # code='custom' providers — pass it explicitly from the template.
                if "custom_mode" in template._fields and template.custom_mode:
                    copy_vals["custom_mode"] = template.custom_mode
                new_provider = template.sudo().copy(copy_vals)
            elif code != "custom":
                # 'custom' providers require a custom_mode field — cannot
                # create safely without knowing the valid selection value.
                # Only create from scratch for non-custom codes (e.g. 'demo').
                new_provider = self.env["payment.provider"].sudo().create({
                    "name": f"{base_name} {abbrev}",
                    "code": code,
                    "company_id": company.id,
                })
            else:
                continue

            # Always set test mode regardless of template state — copying a
            # disabled template would create a disabled provider for the new company.
            new_provider.sudo().write({"state": "test"})
