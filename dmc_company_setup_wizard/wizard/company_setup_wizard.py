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

    # ── Step 2: Bank Account Setup ───────────────────────────────────────────

    bank_account_name = fields.Char("Bank Account Name")
    bank_account_code = fields.Char("Bank Account Code", help="e.g. 101405 — must be unique")
    cash_account_name = fields.Char("Cash Account Name")
    cash_account_code = fields.Char("Cash Account Code", help="Leave blank to skip")
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

    # ── Computed display fields for review step ──────────────────────────────

    @api.depends("company_name")
    def _compute_bank_account_name(self):
        for rec in self:
            if not rec.bank_account_name and rec.company_name:
                rec.bank_account_name = f"Bank {rec.company_name}"

    @api.depends("company_name")
    def _compute_cash_account_name(self):
        for rec in self:
            if not rec.cash_account_name and rec.company_name:
                rec.cash_account_name = f"Cash {rec.company_name}"

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
            if self.cash_account_code:
                existing_cash = self.env["account.account"].sudo().search(
                    [("code", "=", self.cash_account_code)], limit=1
                )
                if existing_cash:
                    raise UserError(
                        _("Cash account code %s already exists. Please choose a unique code.")
                        % self.cash_account_code
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
                self._step2_create_bank_cash_accounts(company)
                self._step3_associate_shared_accounts(company)
                self._step4_copy_taxes_and_groups(company)
                self._step5_create_journals(company)
                self._step6_create_payment_providers(company)
            self.created_company_id = company
            self.result_message = _(
                'Company "%s" was created successfully.\n\n'
                "The following have been set up:\n"
                "  • Bank/Cash chart accounts\n"
                "  • Associations with existing shared chart accounts\n"
                "  • Tax groups and taxes (copied from TITAN Main)\n"
                "  • Journals (Sales, Purchase, Bank, Miscellaneous)\n"
                "  • Payment providers (Cash on Delivery, Demo, Wire Transfer)"
            ) % company.name
            self.state = "done"
        except UserError:
            raise
        except Exception as e:
            raise UserError(
                _("Company setup failed and has been fully rolled back.\n\nError: %s") % str(e)
            ) from e
        return self._reopen_wizard()

    # ── Step implementations ─────────────────────────────────────────────────

    def _step1_create_company(self):
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
        if self.country_id:
            vals["country_id"] = self.country_id.id
        if self.parent_id:
            vals["parent_id"] = self.parent_id.id
        if self.logo:
            vals["logo"] = self.logo
        return self.env["res.company"].sudo().create(vals)

    def _step2_create_bank_cash_accounts(self, company):
        AccountAccount = self.env["account.account"].sudo()
        bank_name = self.bank_account_name or f"Bank {company.name}"
        AccountAccount.create(
            {
                "name": bank_name,
                "code": self.bank_account_code,
                "account_type": "asset_cash",
                "company_ids": [Command.set([company.id])],
            }
        )
        if self.cash_account_code:
            cash_name = self.cash_account_name or f"Cash {company.name}"
            AccountAccount.create(
                {
                    "name": cash_name,
                    "code": self.cash_account_code,
                    "account_type": "asset_cash",
                    "company_ids": [Command.set([company.id])],
                }
            )

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

    def _step3_associate_shared_accounts(self, company):
        # In Odoo 17+, account.account has per-company code mappings stored in a
        # One2many (shown in the "Mapping" tab). The constraint fires the moment
        # company_ids is updated if the new company has no code entry. We must
        # set BOTH company_ids AND the code mapping in a single write() call.
        shared_accounts = self.env["account.account"].sudo().search(
            [
                ("account_type", "!=", "asset_cash"),
                ("company_ids", "!=", False),
            ]
        )
        if not shared_accounts:
            return

        mapping_field, mapping_model = self._find_account_code_mapping_field()

        for account in shared_accounts:
            source_company = account.company_ids[:1]
            existing_code = ""

            if mapping_field and source_company:
                # Read code from the existing mapping record for an existing company.
                mapping_rec = account[mapping_field].filtered(
                    lambda m: m.company_id.id == source_company.id
                )[:1]
                existing_code = mapping_rec.code if mapping_rec else ""
            elif source_company:
                # Fallback: company_dependent field (try with_company read)
                existing_code = account.with_company(source_company).code or ""

            if mapping_field and existing_code:
                # Combine company_ids link + code mapping creation in ONE write
                # so the constraint sees both changes simultaneously.
                account.write({
                    "company_ids": [Command.link(company.id)],
                    mapping_field: [Command.create({
                        "company_id": company.id,
                        "code": existing_code,
                    })],
                })
            elif mapping_field and not existing_code:
                # No code found for source company — skip adding this company
                # to avoid creating an account with an empty code.
                continue
            else:
                # company_dependent fallback: set code before linking company
                # so the constraint check finds the value already in place.
                if existing_code:
                    account.with_company(company).write({"code": existing_code})
                account.write({"company_ids": [Command.link(company.id)]})

    def _step4_copy_taxes_and_groups(self, company):
        titan = self.env["res.company"].sudo().search(
            [("name", "ilike", "TITAN")], limit=1
        )
        if not titan:
            raise UserError(
                _(
                    'Could not find TITAN (Main) company to copy taxes from. '
                    'Please ensure a company with "TITAN" in its name exists.'
                )
            )

        # Copy tax groups first and build old→new id mapping
        group_map = {}
        titan_groups = self.env["account.tax.group"].sudo().search(
            [("company_id", "=", titan.id)]
        )
        for old_group in titan_groups:
            new_group = old_group.sudo().copy({"company_id": company.id})
            group_map[old_group.id] = new_group.id

        # Copy taxes, remapping their tax group to the new company's group
        titan_taxes = self.env["account.tax"].sudo().search(
            [("company_id", "=", titan.id)]
        )
        for old_tax in titan_taxes:
            copy_vals = {
                "company_id": company.id,
                "name": old_tax.name,
            }
            if old_tax.tax_group_id and old_tax.tax_group_id.id in group_map:
                copy_vals["tax_group_id"] = group_map[old_tax.tax_group_id.id]
            old_tax.sudo().copy(copy_vals)

    def _step5_create_journals(self, company):
        Journal = self.env["account.journal"].sudo().with_company(company)

        journal_defs = [
            {"name": "Customer Invoices", "type": "sale", "code": self.journal_sales_prefix},
            {"name": "Vendor Bills", "type": "purchase", "code": self.journal_purchase_prefix},
            {"name": f"Bank {company.name}", "type": "bank", "code": self.journal_bank_prefix},
            {"name": "Miscellaneous Operations", "type": "general", "code": self.journal_misc_prefix},
        ]

        created = self.env["account.journal"].sudo()
        for vals in journal_defs:
            vals["company_id"] = company.id
            created |= Journal.create(vals)

        # Link Bank journal to the bank account created in Step 2
        bank_account = self.env["account.account"].sudo().search(
            [
                ("code", "=", self.bank_account_code),
                ("company_ids", "in", [company.id]),
            ],
            limit=1,
        )
        if bank_account:
            bank_journal = created.filtered(lambda j: j.type == "bank")
            if bank_journal:
                bank_journal.sudo().write({"default_account_id": bank_account.id})

        self.created_journal_ids = [Command.set(created.ids)]

    def _step6_create_payment_providers(self, company):
        titan = self.env["res.company"].sudo().search(
            [("name", "ilike", "TITAN")], limit=1
        )
        if not titan:
            return

        # These are the standard Odoo payment provider codes.
        # Verify exact values against the live DB before deployment:
        #   SELECT code, name FROM payment_provider WHERE company_id = <titan_id>;
        provider_codes = ["custom", "demo", "wire_transfer"]

        for code in provider_codes:
            titan_provider = self.env["payment.provider"].sudo().search(
                [("code", "=", code), ("company_id", "=", titan.id)],
                limit=1,
            )
            if titan_provider:
                titan_provider.sudo().copy({"company_id": company.id})
