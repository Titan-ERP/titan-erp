from odoo import fields, models
from odoo.tools import html_escape


class SaleOrder(models.Model):
    _inherit = "sale.order"

    SOUTHERN_MEMBERSHIP_CODE = "SEC-MEMBERSHIP-STANDARD"
    SOUTHERN_CARD_FEE_CODE = "CARD-FEE"
    SOUTHERN_CARD_FEE_RATE = 0.035
    SOUTHERN_CARD_FEE_FIXED = 0.30
    SOUTHERN_PICKUP_CARRIER = "Pickup at Southern Equipment"
    SOUTHERN_SHIP_CARRIER = "Shipping reviewed after order confirmation"
    SOUTHERN_LEGACY_SHIP_CARRIERS = ("Flat-rate shipping from Southern Equipment",)
    SOUTHERN_PARTS_PORTAL_TAG = "Website Parts Order"
    SOUTHERN_MIN_PARTS_MARGIN_RATE = 0.15
    SOUTHERN_PARTS_FAILURE_EMAIL = "raymy@southernequipment.co"
    SOUTHERN_MEMBER_PARTS_DISCOUNT = 5.0

    southern_cost_verification_status = fields.Selection(
        [
            ("not_checked", "Not Checked"),
            ("ok", "Verified"),
            ("review", "Needs Review"),
            ("loss_risk", "Loss Risk"),
        ],
        default="not_checked",
        copy=False,
    )
    southern_estimated_parts_cost = fields.Monetary(copy=False)
    southern_estimated_parts_revenue = fields.Monetary(copy=False)
    southern_estimated_parts_margin = fields.Monetary(copy=False)
    southern_estimated_parts_margin_rate = fields.Float(copy=False)
    southern_cost_verification_note = fields.Text(copy=False)
    southern_cost_verified_at = fields.Datetime(copy=False)
    southern_parts_review_state = fields.Selection(
        [
            ("not_parts", "Not a Parts Order"),
            ("parts_review", "Parts Desk Review"),
            ("supplier_verification", "Supplier Verification"),
            ("confirmed", "Confirmed"),
            ("ready_for_pickup", "Ready for Pickup"),
            ("shipping_in_progress", "Shipping in Progress"),
            ("needs_customer_confirmation", "Needs Customer Confirmation"),
            ("completed", "Complete"),
        ],
        default="not_parts",
        copy=False,
    )
    southern_parts_review_note = fields.Text(copy=False)
    southern_supplier_confirmation = fields.Char(copy=False)
    southern_supplier_verified_at = fields.Datetime(copy=False)
    southern_pickup_or_shipping_note = fields.Char(copy=False)
    southern_customer_last_update_state = fields.Selection(
        [
            ("parts_review", "Parts Desk Review"),
            ("supplier_verification", "Supplier Verification"),
            ("confirmed", "Confirmed"),
            ("ready_for_pickup", "Ready for Pickup"),
            ("shipping_in_progress", "Shipping in Progress"),
            ("needs_customer_confirmation", "Needs Customer Confirmation"),
            ("completed", "Complete"),
        ],
        copy=False,
    )
    southern_customer_last_update_at = fields.Datetime(copy=False)

    def _cart_add(self, *args, **kwargs):
        result = super()._cart_add(*args, **kwargs)
        self._southern_sync_membership_discount()
        self._southern_sync_website_card_fee()
        self._southern_sync_website_cost_check()
        return result

    def _cart_update_line_quantity(self, *args, **kwargs):
        result = super()._cart_update_line_quantity(*args, **kwargs)
        self._southern_sync_membership_discount()
        self._southern_sync_website_card_fee()
        self._southern_sync_website_cost_check()
        return result

    def action_confirm(self):
        self._southern_sync_membership_discount()
        self._southern_sync_website_card_fee()
        self._southern_sync_website_cost_check()
        self._southern_apply_website_fulfillment_routes()
        result = super().action_confirm()
        self._southern_start_website_parts_review()
        self._southern_note_website_purchase_orders()
        self._southern_auto_advance_parts_review_after_confirm()
        self._southern_activate_paid_memberships()
        return result

    def _southern_is_website_parts_order(self):
        self.ensure_one()
        return bool(self._southern_parts_order_lines_for_cost_check())

    def _southern_start_website_parts_review(self):
        for order in self:
            if (
                not order.website_id
                or order.company_id.id != 2
                or not order._southern_is_website_parts_order()
            ):
                continue
            order._southern_set_parts_review_state(
                "parts_review",
                notify_customer=True,
                internal_note=(
                    "Website parts order received. Cost, availability, and pickup "
                    "or shipping details should be reviewed before fulfillment."
                ),
            )

    def _southern_linked_purchase_orders(self):
        self.ensure_one()
        return self.env["purchase.order"].sudo().search([("origin", "ilike", self.name)])

    def _southern_auto_advance_parts_review_after_confirm(self):
        for order in self:
            if order.southern_parts_review_state == "not_parts":
                continue
            if order.southern_cost_verification_status == "loss_risk":
                order._southern_set_parts_review_state(
                    "needs_customer_confirmation",
                    notify_customer=True,
                    internal_note=(
                        "Automation paused this parts order because the cost check "
                        "found loss risk before fulfillment."
                    ),
                )
                order._southern_notify_parts_review_failure(
                    "Website parts order paused for loss risk",
                    (
                        f"Order {order.name} was paused because the website cost check "
                        "found loss risk before fulfillment."
                    ),
                )
                continue
            if order.southern_cost_verification_status == "review":
                order._southern_notify_parts_review_failure(
                    "Website parts order needs cost review",
                    (
                        f"Order {order.name} needs parts desk review before automation "
                        f"continues.\n\n{order.southern_cost_verification_note or ''}"
                    ),
                )
                continue

            purchase_orders = order._southern_linked_purchase_orders()
            if purchase_orders:
                order._southern_set_parts_review_state(
                    "supplier_verification",
                    notify_customer=True,
                    internal_note=(
                        "Automation found vendor purchasing records for this website "
                        "parts order and moved it to supplier verification."
                    ),
                )
            else:
                order._southern_notify_parts_review_failure(
                    "Website parts order has no vendor PO",
                    (
                        f"Order {order.name} passed cost review but no linked vendor "
                        "purchase order was found after confirmation."
                    ),
                )

    def _southern_notify_parts_review_failure(self, subject, body):
        for order in self:
            marker = f"[Southern parts alert] {subject}"
            if marker in (order.southern_parts_review_note or ""):
                continue
            email_body = (
                f"{html_escape(body)}<br/><br/>"
                f"Customer: {html_escape(order.partner_id.display_name or '')}<br/>"
                f"Order: {html_escape(order.name or '')}<br/>"
                f"Review state: {html_escape(order.southern_parts_review_state or '')}<br/>"
                f"Cost check: {html_escape(order.southern_cost_verification_status or '')}"
            )
            order.sudo().write(
                {
                    "southern_parts_review_note": (
                        f"{order.southern_parts_review_note or ''}\n{marker}"
                    ).strip()
                }
            )
            order.message_post(body=f"<strong>{html_escape(subject)}</strong><br/>{email_body}")
            self.env["mail.mail"].sudo().create(
                {
                    "subject": subject,
                    "body_html": email_body,
                    "email_to": self.SOUTHERN_PARTS_FAILURE_EMAIL,
                    "auto_delete": False,
                }
            ).send()
        return True

    def _cron_southern_auto_advance_parts_review(self):
        orders = self.sudo().search(
            [
                ("website_id", "!=", False),
                ("company_id", "=", 2),
                ("southern_parts_review_state", "not in", ["not_parts", "completed"]),
                ("state", "in", ["sale", "done"]),
            ],
            limit=100,
        )
        for order in orders:
            if order.southern_cost_verification_status in ("not_checked", "ok"):
                order._southern_sync_website_cost_check()
            order._southern_auto_advance_parts_review_after_confirm()
            purchase_orders = order._southern_linked_purchase_orders()
            purchase_orders._southern_advance_linked_parts_orders_from_purchase()
            order.picking_ids._southern_advance_parts_orders_from_picking()
        return True

    def _southern_customer_update_body(self, state):
        self.ensure_one()
        messages = {
            "parts_review": (
                "We received your parts order. The Southern Equipment parts desk "
                "is reviewing availability, fitment, and pickup or shipping details."
            ),
            "supplier_verification": (
                "Your parts order is being verified with our supplier. We will update "
                "you when fulfillment details are confirmed."
            ),
            "confirmed": (
                "Your parts order has been confirmed. We will update you when it is "
                "ready for pickup or shipment."
            ),
            "ready_for_pickup": (
                "Your parts order is ready for pickup at Southern Equipment."
            ),
            "shipping_in_progress": (
                "Your parts order is moving through shipping. Tracking or delivery "
                "details will be shared when available."
            ),
            "needs_customer_confirmation": (
                "Your parts order needs confirmation before fulfillment. A Southern "
                "Equipment team member will contact you with the next step."
            ),
            "completed": "Your parts order is complete. Thank you for choosing Southern Equipment.",
        }
        return messages.get(state)

    def _southern_set_parts_review_state(
        self,
        state,
        notify_customer=False,
        internal_note=None,
        customer_note=None,
    ):
        for order in self:
            vals = {"southern_parts_review_state": state}
            if state in ("supplier_verification", "confirmed"):
                vals["southern_supplier_verified_at"] = fields.Datetime.now()
            if internal_note:
                vals["southern_parts_review_note"] = (
                    f"{order.southern_parts_review_note or ''}\n{internal_note}"
                ).strip()
            order.sudo().write(vals)

            if internal_note:
                order.message_post(body=html_escape(internal_note))

            if notify_customer and order.southern_customer_last_update_state != state:
                body = customer_note or order._southern_customer_update_body(state)
                if body:
                    order.message_post(
                        body=html_escape(body),
                        partner_ids=order.partner_id.ids,
                        subtype_xmlid="mail.mt_comment",
                    )
                    order.sudo().write(
                        {
                            "southern_customer_last_update_state": state,
                            "southern_customer_last_update_at": fields.Datetime.now(),
                        }
                    )
        return True

    def action_southern_parts_supplier_verification(self):
        return self._southern_set_parts_review_state(
            "supplier_verification",
            notify_customer=True,
            internal_note="Parts order moved to supplier verification.",
        )

    def action_southern_parts_confirmed(self):
        return self._southern_set_parts_review_state(
            "confirmed",
            notify_customer=True,
            internal_note="Parts order cost, availability, and fulfillment details confirmed.",
        )

    def action_southern_parts_ready_for_pickup(self):
        return self._southern_set_parts_review_state(
            "ready_for_pickup",
            notify_customer=True,
            internal_note="Parts order marked ready for pickup.",
        )

    def action_southern_parts_shipping_in_progress(self):
        return self._southern_set_parts_review_state(
            "shipping_in_progress",
            notify_customer=True,
            internal_note="Parts order marked shipping in progress.",
        )

    def action_southern_parts_needs_customer_confirmation(self):
        return self._southern_set_parts_review_state(
            "needs_customer_confirmation",
            notify_customer=True,
            internal_note="Parts order needs customer confirmation before fulfillment.",
        )

    def action_southern_parts_completed(self):
        return self._southern_set_parts_review_state(
            "completed",
            notify_customer=True,
            internal_note="Parts order review and fulfillment marked complete.",
        )

    def _southern_parts_order_lines_for_cost_check(self):
        self.ensure_one()
        return self.order_line.filtered(
            lambda line: not line.display_type
            and not line.is_delivery
            and line.product_id.type != "service"
            and line.product_id.default_code
            not in (self.SOUTHERN_MEMBERSHIP_CODE, self.SOUTHERN_CARD_FEE_CODE)
        )

    def _southern_has_active_membership_discount(self):
        self.ensure_one()
        partner = self.partner_id.commercial_partner_id
        if not partner or partner.southern_partner_status in ("approved", "active"):
            return False
        return (
            partner.southern_account_type == "member"
            and partner.southern_membership_status == "active"
        )

    def _southern_sync_membership_discount(self):
        for order in self:
            if not order.website_id or order.state not in ("draft", "sent"):
                continue

            discount = (
                self.SOUTHERN_MEMBER_PARTS_DISCOUNT
                if order._southern_has_active_membership_discount()
                else 0.0
            )
            for line in order._southern_parts_order_lines_for_cost_check():
                if line.discount != discount:
                    line.sudo().discount = discount
        return True

    def _southern_vendor_unit_cost(self, line):
        product = line.product_id
        seller = product.seller_ids[:1]
        if seller:
            supplier_currency = seller.currency_id or line.currency_id
            return supplier_currency._convert(
                seller.price,
                line.currency_id,
                line.company_id,
                fields.Date.context_today(line),
            )

        if product.standard_price:
            return product.currency_id._convert(
                product.standard_price,
                line.currency_id,
                line.company_id,
                fields.Date.context_today(line),
            )
        return 0.0

    def _southern_sync_website_cost_check(self):
        for order in self:
            if not order.website_id or order.state not in ("draft", "sent"):
                continue

            revenue = 0.0
            estimated_cost = 0.0
            missing_cost_lines = []
            loss_lines = []
            thin_margin_lines = []

            for line in order._southern_parts_order_lines_for_cost_check():
                line_revenue = line.price_subtotal
                unit_cost = order._southern_vendor_unit_cost(line)
                line_cost = order.currency_id.round(unit_cost * line.product_uom_qty)
                line_margin = line_revenue - line_cost

                revenue += line_revenue
                estimated_cost += line_cost

                product_label = line.product_id.default_code or line.product_id.display_name
                if not unit_cost:
                    missing_cost_lines.append(product_label)
                    continue
                if line_margin < 0:
                    loss_lines.append(product_label)
                    continue
                if line_revenue and (line_margin / line_revenue) < self.SOUTHERN_MIN_PARTS_MARGIN_RATE:
                    thin_margin_lines.append(product_label)

            margin = revenue - estimated_cost
            margin_rate = margin / revenue if revenue else 0.0
            status = "ok"
            note_lines = []

            if not revenue:
                status = "not_checked"
                note_lines.append("No parts lines are currently in the website cart.")
            if missing_cost_lines:
                status = "review"
                note_lines.append(
                    "Missing vendor/standard cost: %s." % ", ".join(missing_cost_lines)
                )
            if thin_margin_lines:
                status = "review"
                note_lines.append(
                    "Below target margin: %s." % ", ".join(thin_margin_lines)
                )
            if loss_lines or margin < 0:
                status = "loss_risk"
                if loss_lines:
                    note_lines.append(
                        "Known cost exceeds sale price: %s." % ", ".join(loss_lines)
                    )
                if margin < 0:
                    note_lines.append("Cart total estimated parts margin is negative.")
            if status == "ok":
                note_lines.append("Website parts cost check passed using available vendor/standard cost.")

            order.sudo().write(
                {
                    "southern_cost_verification_status": status,
                    "southern_estimated_parts_cost": order.currency_id.round(estimated_cost),
                    "southern_estimated_parts_revenue": order.currency_id.round(revenue),
                    "southern_estimated_parts_margin": order.currency_id.round(margin),
                    "southern_estimated_parts_margin_rate": margin_rate,
                    "southern_cost_verification_note": "\n".join(note_lines),
                    "southern_cost_verified_at": fields.Datetime.now(),
                }
            )

    def _southern_customer_cost_review_message(self):
        self.ensure_one()
        if self.southern_cost_verification_status == "loss_risk":
            return (
                "This parts cart needs Southern Equipment review before online payment. "
                "Our parts desk will confirm cost, availability, and fulfillment before collecting payment."
            )
        if self.southern_cost_verification_status == "review":
            return (
                "Southern Equipment is verifying supplier cost and availability for this cart. "
                "You can continue building the cart; final fulfillment is confirmed by our parts desk."
            )
        return False

    def _southern_apply_website_fulfillment_routes(self):
        Route = self.env["stock.route"].sudo()
        buy_route = Route.search([("name", "=", "Buy")], limit=1)
        mto_route = Route.search([("name", "=", "Replenish on Order (MTO)")], limit=1)

        vendor_to_southern_routes = (mto_route | buy_route).ids

        for order in self:
            if (
                not order.website_id
                or order.company_id.id != 2
                or order.state not in ("draft", "sent")
                or not order.carrier_id
            ):
                continue

            is_pickup = order.carrier_id.name == self.SOUTHERN_PICKUP_CARRIER
            is_ship = order.carrier_id.name in (
                self.SOUTHERN_SHIP_CARRIER,
                *self.SOUTHERN_LEGACY_SHIP_CARRIERS,
            )
            if not is_pickup and not is_ship:
                continue

            if not vendor_to_southern_routes:
                order.message_post(
                    body=(
                        "Website parts order could not be routed to purchasing because "
                        "the Buy and Replenish on Order routes were not found."
                    )
                )
                continue

            routed_lines = self.env["sale.order.line"]
            skipped_lines = self.env["sale.order.line"]
            for line in order.order_line.filtered(
                lambda order_line: not order_line.display_type
                and not order_line.is_delivery
                and order_line.product_id.default_code
                not in (self.SOUTHERN_MEMBERSHIP_CODE, self.SOUTHERN_CARD_FEE_CODE)
            ):
                product = line.product_id
                if not product.purchase_ok or not product.seller_ids:
                    skipped_lines |= line
                    continue
                line.route_ids = [(6, 0, vendor_to_southern_routes)]
                routed_lines |= line

            if routed_lines:
                order.message_post(
                    body=(
                        "Website parts fulfillment: routed %s line(s) to vendor purchasing "
                        "for receipt at Southern Equipment before customer fulfillment."
                    )
                    % len(routed_lines)
                )
            if skipped_lines:
                order.message_post(
                    body=(
                        "Website parts fulfillment needs review: %s line(s) were not routed "
                        "to purchasing because the product is not purchasable or has no vendor."
                    )
                    % len(skipped_lines)
                )

    def _southern_note_website_purchase_orders(self):
        PurchaseOrder = self.env["purchase.order"].sudo()
        for order in self:
            if not order.website_id or order.company_id.id != 2:
                continue

            purchase_orders = PurchaseOrder.search([("origin", "ilike", order.name)])
            if not purchase_orders:
                continue

            carrier_note = order.carrier_id.name or "No delivery method selected"
            note = (
                "Website Parts Order\n"
                f"Customer order: {order.name}\n"
                f"Customer: {order.partner_id.display_name}\n"
                f"Delivery choice: {carrier_note}\n"
                "Fulfillment rule: order vendor stock to Southern Equipment first; "
                "then fulfill pickup or customer shipment from Southern."
            )
            note_html = "<br/>".join(html_escape(line) for line in note.splitlines())
            for purchase_order in purchase_orders:
                if self.SOUTHERN_PARTS_PORTAL_TAG not in (purchase_order.note or ""):
                    purchase_order.note = (
                        f"{note_html}<br/><br/>{purchase_order.note or ''}"
                    ).strip()
                purchase_order.message_post(body=note_html)

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
            }
            if fee_lines:
                primary_fee_line = fee_lines[0]
                if (
                    primary_fee_line.price_unit != fee_amount
                    or primary_fee_line.product_uom_qty != 1.0
                ):
                    primary_fee_line.with_context(skip_southern_card_fee=True).write(
                        {
                            "name": "Processing Fee",
                            "product_uom_qty": 1.0,
                            "price_unit": fee_amount,
                        }
                    )
                extra_fee_lines = fee_lines - primary_fee_line
                if extra_fee_lines:
                    extra_fee_lines.with_context(skip_southern_card_fee=True).unlink()
            else:
                fee_line = self.env["sale.order.line"].with_context(
                    skip_southern_card_fee=True
                ).sudo().create(vals)
                fee_line.with_context(skip_southern_card_fee=True).write(
                    {"price_unit": fee_amount}
                )

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
        phone = partner.phone or self.partner_id.phone
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
