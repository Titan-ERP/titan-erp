from odoo import fields, models


class SaleOrder(models.Model):
    _inherit = "sale.order"

    SOUTHERN_MEMBERSHIP_CODE = "SEC-MEMBERSHIP-STANDARD"
    SOUTHERN_CARD_FEE_CODE = "CARD-FEE"
    SOUTHERN_CARD_FEE_RATE = 0.035
    SOUTHERN_CARD_FEE_FIXED = 0.30

    def _cart_add(self, *args, **kwargs):
        result = super()._cart_add(*args, **kwargs)
        self._southern_sync_website_card_fee()
        return result

    def _cart_update_line_quantity(self, *args, **kwargs):
        result = super()._cart_update_line_quantity(*args, **kwargs)
        self._southern_sync_website_card_fee()
        return result

    def action_confirm(self):
        result = super().action_confirm()
        self._southern_activate_paid_memberships()
        return result

    def _southern_sync_website_card_fee(self):
        if self.env.context.get("skip_southern_card_fee"):
            return

        fee_product = self.env["product.product"].sudo().search(
            [("default_code", "=", self.SOUTHERN_CARD_FEE_CODE)],
            limit=1,
        )
        if not fee_product:
            return

        for order in self:
            if not order.website_id or order.state not in ("draft", "sent"):
                continue

            fee_lines = order.order_line.filtered(
                lambda line: line.product_id.default_code == self.SOUTHERN_CARD_FEE_CODE
            )
            eligible_lines = order.order_line.filtered(
                lambda line: not line.display_type
                and line.product_id.default_code != self.SOUTHERN_CARD_FEE_CODE
            )
            eligible_subtotal = sum(eligible_lines.mapped("price_subtotal"))

            if eligible_subtotal <= 0:
                fee_lines.with_context(skip_southern_card_fee=True).unlink()
                continue

            fee_amount = order.currency_id.round(
                (eligible_subtotal * self.SOUTHERN_CARD_FEE_RATE)
                + self.SOUTHERN_CARD_FEE_FIXED
            )
            vals = {
                "order_id": order.id,
                "product_id": fee_product.id,
                "name": "Processing Fee",
                "product_uom_qty": 1.0,
                "price_unit": fee_amount,
            }
            if fee_lines:
                primary_fee_line = fee_lines[0]
                if (
                    primary_fee_line.price_unit != fee_amount
                    or primary_fee_line.product_uom_qty != 1.0
                ):
                    primary_fee_line.with_context(skip_southern_card_fee=True).write(vals)
                extra_fee_lines = fee_lines - primary_fee_line
                if extra_fee_lines:
                    extra_fee_lines.with_context(skip_southern_card_fee=True).unlink()
            else:
                self.env["sale.order.line"].with_context(
                    skip_southern_card_fee=True
                ).sudo().create(vals)

    def _southern_membership_lines(self):
        return self.order_line.filtered(
            lambda line: line.product_id.product_tmpl_id.default_code
            == self.SOUTHERN_MEMBERSHIP_CODE
        )

    def _southern_activate_paid_memberships(self):
        for order in self:
            if order.state not in ("sale", "done") or not order._southern_membership_lines():
                continue
            partner = order.partner_id.commercial_partner_id
            order._southern_ensure_membership_application(partner)
            order._southern_ensure_portal_user(partner)

    def _southern_ensure_membership_application(self, partner):
        self.ensure_one()
        application = self.env["southern.membership.application"].sudo().search(
            [("partner_id", "child_of", partner.id)],
            order="create_date desc, id desc",
            limit=1,
        )
        vals = {
            "state": "active",
            "monthly_fee": 25.0,
            "house_credit_limit": 2500.0,
            "requested_house_credit": 2500.0,
            "parts_service_discount": 5.0,
            "payment_authorized": True,
            "agreement_accepted": True,
            "notes": (
                "Activated from paid website membership checkout on sale order "
                f"{self.name}. Card details are stored only by the payment provider."
            ),
        }
        if application:
            application.write(vals)
            return application

        email = partner.email or self.partner_id.email
        phone = partner.phone or partner.mobile or self.partner_id.phone or self.partner_id.mobile
        vals.update(
            {
                "partner_id": partner.id,
                "company_id": self.company_id.id,
                "member_name": partner.name,
                "phone": phone or "Provided during checkout",
                "email": email or "Provided during checkout",
                "signature": "Accepted through online membership checkout",
                "signed_on": fields.Datetime.now(),
                "billing_email": email,
                "billing_phone": phone,
                "cardholder_name": partner.name,
            }
        )
        return self.env["southern.membership.application"].sudo().create(vals)

    def _southern_ensure_portal_user(self, partner):
        self.ensure_one()
        email = (partner.email or self.partner_id.email or "").strip().lower()
        if not email:
            self.message_post(
                body=(
                    "Membership was activated, but no portal invitation was sent "
                    "because the customer does not have an email address."
                )
            )
            return False

        Users = self.env["res.users"].sudo().with_context(active_test=False)
        portal_group = self.env.ref("base.group_portal")
        user = Users.search([("login", "=", email)], limit=1)
        if not user:
            user = Users.with_context(no_reset_password=True).create(
                {
                    "name": partner.name,
                    "login": email,
                    "email": email,
                    "partner_id": partner.id,
                    "company_id": self.company_id.id,
                    "company_ids": [(6, 0, [self.company_id.id])],
                    "groups_id": [(6, 0, [portal_group.id])],
                }
            )
        elif portal_group not in user.groups_id:
            user.write({"groups_id": [(4, portal_group.id)]})

        if not user.active:
            user.active = True
        user.action_reset_password()
        self.message_post(
            body=f"Membership portal access activated and an invitation was sent to {email}."
        )
        return user
