from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

READINESS_SELECTION = [
    ("disabled", "Not Enabled"),
    ("blocked", "Blocked"),
    ("warning", "Review"),
    ("ready", "Ready"),
]

REVENUE_BUCKETS = [
    ("parts", "Parts Revenue"),
    ("service", "Service Revenue"),
    ("rental", "Rental Revenue"),
    ("equipment", "Equipment Revenue"),
    ("fees", "Fees"),
    ("other", "Other Revenue"),
]

PURCHASE_PURPOSES = [
    ("inventory", "Inventory / Stock"),
    ("parts_cost", "Parts Direct Cost"),
    ("service_cost", "Service Direct Cost"),
    ("rental_cost", "Rental Direct Cost"),
    ("equipment_cost", "Equipment Direct Cost"),
    ("operating_expense", "Operating Expense"),
    ("fixed_asset", "Fixed Asset"),
    ("prepaid", "Prepaid Expense / Deposit"),
    ("other", "Other (Accounting Review)"),
]


class SouthernCommercialGuardrailMixin(models.AbstractModel):
    _name = "southern.commercial.guardrail.mixin"
    _description = "Southern Commercial Accounting Guardrail"

    southern_accounting_readiness_state = fields.Selection(
        READINESS_SELECTION,
        string="Accounting Readiness",
        compute="_compute_southern_accounting_readiness",
    )
    southern_accounting_issue_count = fields.Integer(
        string="Accounting Issues",
        compute="_compute_southern_accounting_readiness",
    )
    southern_accounting_issue_summary = fields.Text(
        string="Accounting Review",
        compute="_compute_southern_accounting_readiness",
    )
    southern_accounting_exception_reason = fields.Char(
        string="Commercial Exception Reason",
        tracking=True,
        help="Required when an intentional zero-price line must be confirmed.",
    )

    def _southern_policy(self):
        self.ensure_one()
        return self.env["southern.accounting.policy"].find_company_policy(self.company_id)

    def _southern_guardrail_is_enforced(self, policy):
        self.ensure_one()
        if not policy or policy.commercial_order_guardrail_mode != "enforce":
            return False
        effective_at = policy.commercial_order_guardrail_effective_at
        return not effective_at or not self.create_date or self.create_date >= effective_at

    def _southern_accounting_issues(self):
        return [], []

    def _compute_southern_accounting_readiness(self):
        for order in self:
            policy = order._southern_policy()
            if not policy or policy.commercial_order_guardrail_mode == "off":
                order.southern_accounting_readiness_state = "disabled"
                order.southern_accounting_issue_count = 0
                order.southern_accounting_issue_summary = False
                continue
            blockers, warnings = order._southern_accounting_issues()
            order.southern_accounting_issue_count = len(blockers) + len(warnings)
            order.southern_accounting_issue_summary = (
                "\n".join(
                    [*(f"BLOCK: {message}" for message in blockers), *(f"REVIEW: {message}" for message in warnings)]
                )
                or False
            )
            if blockers:
                order.southern_accounting_readiness_state = "blocked"
            elif warnings:
                order.southern_accounting_readiness_state = "warning"
            else:
                order.southern_accounting_readiness_state = "ready"

    def _validate_southern_accounting_confirmation(self):
        for order in self:
            policy = order._southern_policy()
            blockers, _warnings = order._southern_accounting_issues()
            if blockers and order._southern_guardrail_is_enforced(policy):
                raise ValidationError(
                    _("Correct these accounting issues before confirmation:\n- %s") % "\n- ".join(blockers)
                )


class SaleOrder(models.Model):
    _inherit = ["sale.order", "southern.commercial.guardrail.mixin"]  # noqa: RUF012
    _name = "sale.order"

    @api.depends(
        "company_id",
        "partner_id",
        "southern_quote_type",
        "payment_term_id",
        "southern_accounting_exception_reason",
        "order_line.product_id",
        "order_line.product_uom_qty",
        "order_line.price_unit",
        "order_line.discount",
        "order_line.tax_ids",
        "order_line.product_id.standard_price",
        "order_line.product_id.product_tmpl_id.southern_revenue_bucket",
        "order_line.product_id.categ_id.southern_accounting_bucket",
        "order_line.southern_revenue_bucket",
        "order_line.southern_revenue_account_id",
    )
    def _compute_southern_accounting_readiness(self):
        return super()._compute_southern_accounting_readiness()

    def _southern_accounting_issues(self):
        self.ensure_one()
        blockers = []
        warnings = []
        policy = self._southern_policy()
        lines = self.order_line.filtered(lambda line: not line.display_type)
        if not self.partner_id:
            blockers.append(_("Select a customer."))
        if not lines:
            blockers.append(_("Add at least one product line."))
        if self.southern_quote_type == "general" and not ("website_id" in self._fields and self.website_id):
            warnings.append(_("Choose Parts, Service, Equipment Sale, or Rental when one applies."))
        for line in lines:
            label = line.product_id.display_name or line.name or _("unnamed line")
            if not line.product_id:
                blockers.append(_("%s: select a product.") % label)
                continue
            if line.product_uom_qty <= 0:
                blockers.append(_("%s: quantity must be greater than zero.") % label)
            if line.price_unit < 0:
                blockers.append(_("%s: sales price cannot be negative.") % label)
            elif not line.price_unit and not self.southern_accounting_exception_reason:
                blockers.append(_("%s: enter a sales price or an exception reason.") % label)
            if line.discount < 0 or line.discount > 100:
                blockers.append(_("%s: discount must be between 0%% and 100%%.") % label)
            if policy and policy.require_sale_tax_selection and not line.tax_ids:
                blockers.append(_("%s: select the applicable sales tax (a 0%% tax is allowed).") % label)
            bucket = line._southern_resolved_revenue_bucket()
            account = line._southern_resolved_revenue_account()
            if not bucket or bucket == "other":
                blockers.append(_("%s: select its revenue type.") % label)
            if not account:
                blockers.append(_("%s: no revenue account is configured for its revenue type.") % label)
            elif account.account_type not in ("income", "income_other"):
                blockers.append(_("%s: account %s is not a revenue account.") % (label, account.display_name))
            expected = policy.get_revenue_account(bucket) if policy and bucket else False
            if expected and account and account != expected:
                blockers.append(
                    _("%s: revenue must post to %s, not %s.") % (label, expected.display_name, account.display_name)
                )
            net_price = line.price_unit * (1.0 - (line.discount or 0.0) / 100.0)
            product_cost = line.product_id.with_company(line.company_id).standard_price
            if product_cost > 0 and net_price < product_cost:
                warnings.append(
                    _("%s: net sales price is below the current product cost; confirm this is intentional.") % label
                )
        product_lines = lines.filtered("product_id")
        if len(product_lines.mapped("product_id")) < len(product_lines):
            warnings.append(_("The same product appears on multiple lines; confirm it was not duplicated."))
        if not self.payment_term_id:
            warnings.append(_("No payment term is selected; Odoo will treat the balance as immediately due."))
        return blockers, warnings

    def action_confirm(self):
        self._validate_southern_accounting_confirmation()
        return super().action_confirm()


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    southern_revenue_bucket = fields.Selection(REVENUE_BUCKETS, string="Revenue Type", copy=True)
    southern_revenue_account_id = fields.Many2one(
        "account.account",
        string="Revenue Account",
        domain="[('account_type', 'in', ['income', 'income_other']), ('company_ids', 'in', company_id)]",
        copy=True,
        check_company=True,
    )

    def _southern_resolved_revenue_bucket(self):
        self.ensure_one()
        template = self.product_id.product_tmpl_id
        bucket = (
            self.southern_revenue_bucket
            or template.southern_revenue_bucket
            or template.categ_id.southern_accounting_bucket
        )
        if bucket:
            return bucket
        quote_type = self.order_id.southern_quote_type
        if quote_type == "general" and "website_id" in self.order_id._fields and self.order_id.website_id:
            return "parts"
        if quote_type == "parts":
            return "parts"
        if quote_type == "service":
            return "service" if self.product_id.type == "service" else "parts"
        return {"equipment_sale": "equipment", "rental": "rental"}.get(quote_type)

    def _southern_resolved_revenue_account(self):
        self.ensure_one()
        policy = self.env["southern.accounting.policy"].find_company_policy(self.company_id)
        expected = policy.get_revenue_account(self._southern_resolved_revenue_bucket()) if policy else False
        product = self.product_id.with_company(self.company_id)
        return (
            self.southern_revenue_account_id
            or expected
            or product.property_account_income_id
            or product.categ_id.property_account_income_categ_id
        )

    @api.onchange("product_id", "order_id.southern_quote_type")
    def _onchange_southern_revenue_routing(self):
        for line in self:
            if not line.product_id:
                continue
            bucket = line._southern_resolved_revenue_bucket()
            if not line.southern_revenue_bucket and bucket:
                line.southern_revenue_bucket = bucket
            if not line.southern_revenue_account_id:
                line.southern_revenue_account_id = line._southern_resolved_revenue_account()

    def _prepare_invoice_line(self, **optional_values):
        values = super()._prepare_invoice_line(**optional_values)
        if not self.display_type and not self.is_downpayment and self.product_id.type != "combo":
            account = self._southern_resolved_revenue_account()
            if account:
                values["account_id"] = account.id
        return values


class PurchaseOrder(models.Model):
    _inherit = ["purchase.order", "southern.commercial.guardrail.mixin"]  # noqa: RUF012
    _name = "purchase.order"

    @api.depends(
        "company_id",
        "partner_id",
        "partner_ref",
        "payment_term_id",
        "southern_accounting_exception_reason",
        "order_line.product_id",
        "order_line.product_qty",
        "order_line.price_unit",
        "order_line.discount",
        "order_line.tax_ids",
        "order_line.product_id.is_storable",
        "order_line.product_id.categ_id.property_valuation",
        "order_line.product_id.categ_id.property_stock_valuation_account_id",
        "order_line.southern_demand_type",
        "order_line.analytic_distribution",
        "order_line.southern_service_case_id",
        "order_line.southern_task_id",
        "order_line.southern_repair_order_id",
        "order_line.southern_purchase_purpose",
        "order_line.southern_expense_account_id",
    )
    def _compute_southern_accounting_readiness(self):
        return super()._compute_southern_accounting_readiness()

    def _southern_accounting_issues(self):
        self.ensure_one()
        blockers = []
        warnings = []
        policy = self._southern_policy()
        lines = self.order_line.filtered(lambda line: not line.display_type)
        if not self.partner_id:
            blockers.append(_("Select a vendor."))
        if not lines:
            blockers.append(_("Add at least one product line."))
        for line in lines:
            label = line.product_id.display_name or line.name or _("unnamed line")
            if not line.product_id:
                blockers.append(_("%s: select a product.") % label)
                continue
            if line.product_qty <= 0:
                blockers.append(_("%s: quantity must be greater than zero.") % label)
            if line.price_unit < 0:
                blockers.append(_("%s: unit cost cannot be negative.") % label)
            elif not line.price_unit and not self.southern_accounting_exception_reason:
                blockers.append(_("%s: enter a unit cost or an exception reason.") % label)
            if line.discount < 0 or line.discount > 100:
                blockers.append(_("%s: discount must be between 0%% and 100%%.") % label)
            if policy and policy.require_purchase_tax_selection and not line.tax_ids:
                blockers.append(_("%s: select the applicable purchase tax (a 0%% tax is allowed).") % label)
            purpose = line._southern_resolved_purchase_purpose()
            if not purpose or purpose == "other":
                blockers.append(_("%s: select what the purchase is for.") % label)
                continue
            if purpose == "inventory":
                if not line.product_id.is_storable:
                    blockers.append(_("%s: only a storable product can be classified as inventory.") % label)
                category = line.product_id.categ_id
                if category.property_valuation == "real_time" and not category.property_stock_valuation_account_id:
                    blockers.append(_("%s: its category has no stock valuation account.") % label)
                elif category.property_valuation != "real_time":
                    expense_account = line._southern_resolved_expense_account()
                    if not expense_account:
                        blockers.append(_("%s: periodic inventory has no purchase expense account.") % label)
                    warnings.append(
                        _("%s: its category uses periodic valuation, so purchases do not update perpetual inventory.")
                        % label
                    )
                continue
            account = line._southern_resolved_expense_account()
            if not account:
                blockers.append(_("%s: select the expense or asset account.") % label)
                continue
            allowed_types = line._southern_allowed_account_types(purpose)
            if account.account_type not in allowed_types:
                blockers.append(
                    _("%s: %s is not valid for %s.")
                    % (label, account.display_name, dict(PURCHASE_PURPOSES).get(purpose))
                )
            expected = line._southern_policy_cost_account(policy, purpose)
            if expected and account != expected:
                blockers.append(
                    _("%s: direct cost must post to %s, not %s.") % (label, expected.display_name, account.display_name)
                )
            if purpose in ("parts_cost", "service_cost", "rental_cost", "equipment_cost"):
                has_job_link = bool(
                    line.analytic_distribution
                    or line.southern_service_case_id
                    or line.southern_task_id
                    or line.southern_repair_order_id
                )
                if not has_job_link:
                    warnings.append(
                        _("%s: direct cost is not linked to a service job, repair, or analytic allocation.") % label
                    )
        product_lines = lines.filtered("product_id")
        if len(product_lines.mapped("product_id")) < len(product_lines):
            warnings.append(_("The same product appears on multiple lines; confirm it was not duplicated."))
        if not self.payment_term_id:
            warnings.append(_("No vendor payment term is selected; confirm the vendor's due date terms."))
        if not self.partner_ref:
            warnings.append(_("Vendor reference is blank; add the quote, order, or invoice reference when available."))
        return blockers, warnings

    def button_confirm(self):
        self._validate_southern_accounting_confirmation()
        return super().button_confirm()


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    southern_purchase_purpose = fields.Selection(PURCHASE_PURPOSES, string="Purchase Purpose", copy=True)
    southern_expense_account_id = fields.Many2one(
        "account.account",
        string="Expense / Asset Account",
        domain="[('company_ids', 'in', company_id)]",
        copy=True,
        check_company=True,
    )

    def _southern_resolved_purchase_purpose(self):
        self.ensure_one()
        if self.southern_purchase_purpose:
            return self.southern_purchase_purpose
        if self.southern_demand_type == "service":
            return "service_cost"
        if self.southern_demand_type == "internal":
            return "operating_expense"
        if self.southern_demand_type == "sale":
            return "parts_cost"
        return "inventory" if self.product_id.is_storable else "operating_expense"

    def _southern_policy_cost_account(self, policy, purpose):
        if not policy:
            return False
        bucket = {
            "parts_cost": "parts",
            "service_cost": "service",
            "rental_cost": "rental",
            "equipment_cost": "equipment",
        }.get(purpose)
        return policy.get_cost_account(bucket) if bucket else False

    def _southern_resolved_expense_account(self):
        self.ensure_one()
        purpose = self._southern_resolved_purchase_purpose()
        policy = self.env["southern.accounting.policy"].find_company_policy(self.company_id)
        expected = self._southern_policy_cost_account(policy, purpose)
        product = self.product_id.with_company(self.company_id)
        return (
            self.southern_expense_account_id
            or expected
            or product.property_account_expense_id
            or product.categ_id.property_account_expense_categ_id
        )

    @api.model
    def _southern_allowed_account_types(self, purpose):
        if purpose == "fixed_asset":
            return ("asset_fixed", "asset_non_current")
        if purpose == "prepaid":
            return ("asset_prepayments", "asset_current")
        return ("expense", "expense_direct_cost", "expense_other", "expense_depreciation")

    @api.onchange("product_id", "southern_demand_type")
    def _onchange_southern_purchase_routing(self):
        for line in self:
            if not line.product_id:
                continue
            purpose = line._southern_resolved_purchase_purpose()
            if not line.southern_purchase_purpose:
                line.southern_purchase_purpose = purpose
            if purpose != "inventory" and not line.southern_expense_account_id:
                line.southern_expense_account_id = line._southern_resolved_expense_account()

    def _prepare_account_move_line(self, *args, **kwargs):
        values = super()._prepare_account_move_line(*args, **kwargs)
        if not self.display_type and self._southern_resolved_purchase_purpose() != "inventory":
            account = self._southern_resolved_expense_account()
            if account:
                values["account_id"] = account.id
        return values
