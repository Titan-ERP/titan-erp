from urllib.parse import urlencode

from odoo import http
from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager
from odoo.http import request


class SouthernCustomerPortal(CustomerPortal):
    def _is_public_user(self):
        return request.env.user._is_public()

    def _partner_domain(self):
        partner = request.env.user.partner_id.commercial_partner_id
        return [("partner_id", "child_of", partner.id)]

    def _repair_order_domain(self):
        return self._partner_domain() + [("state", "not in", ["done", "cancel"])]

    def _outstanding_invoice_domain(self):
        return self._partner_domain() + [
            ("move_type", "=", "out_invoice"),
            ("state", "=", "posted"),
            ("amount_residual", ">", 0),
        ]

    def _membership_domain(self):
        partner = request.env.user.partner_id.commercial_partner_id
        return [("partner_id", "child_of", partner.id)]

    def _get_membership_application(self):
        return request.env["southern.membership.application"].sudo().search(
            self._membership_domain(),
            order="create_date desc, id desc",
            limit=1,
        )

    def _has_active_membership(self):
        application = self._get_membership_application()
        return bool(application and application.state == "active")

    def _login_redirect_url(self, target):
        return "/web/login?%s" % urlencode({"redirect": target})

    def _membership_product_url(self):
        product = request.env["product.template"].sudo().search(
            [("default_code", "=", "SEC-MEMBERSHIP-STANDARD")],
            limit=1,
        )
        return product.website_url if product and product.website_url else "/membership"

    def _add_customer_access_values(self, values):
        if not self._is_public_user():
            application = self._get_membership_application()
            values.update(
                {
                    "southern_membership_application": application,
                    "southern_is_premium_customer": bool(
                        application and application.state == "active"
                    ),
                }
            )
        return values

    def _prepare_home_portal_values(self, counters):
        values = super()._prepare_home_portal_values(counters)
        self._add_customer_access_values(values)
        if "membership_application_count" in counters:
            values["membership_application_count"] = request.env[
                "southern.membership.application"
            ].sudo().search_count(self._membership_domain())
        if "repair_order_count" in counters:
            values["repair_order_count"] = request.env["repair.order"].sudo().search_count(
                self._repair_order_domain()
            )
        if "outstanding_invoice_count" in counters:
            values["outstanding_invoice_count"] = request.env["account.move"].sudo().search_count(
                self._outstanding_invoice_domain()
            )
        return values

    @http.route("/account-access", type="http", auth="public", website=True)
    def southern_account_access(self, **kw):
        if not self._is_public_user():
            return request.redirect("/my/home")
        return request.render(
            "southern_customer_portal.southern_account_access",
            {
                "customer_login_url": self._login_redirect_url("/my/home"),
                "membership_login_url": self._login_redirect_url("/my/home?premium=1"),
                "partner_login_url": self._login_redirect_url("/my/home?partner=1"),
                "membership_signup_url": "/membership-sign-up",
                "partner_application_url": "/partner-application",
            },
        )

    @http.route("/customer-login", type="http", auth="public", website=True)
    def southern_customer_login(self, **kw):
        if not self._is_public_user():
            return request.redirect("/my/home")
        return request.redirect(self._login_redirect_url("/my/home"))

    @http.route("/membership-login", type="http", auth="public", website=True)
    def southern_membership_login(self, **kw):
        if self._is_public_user():
            return request.redirect(self._login_redirect_url("/my/home?premium=1"))
        if self._has_active_membership():
            return request.redirect("/my/home")
        return request.redirect("/my/membership")

    @http.route("/partner-login", type="http", auth="public", website=True)
    def southern_partner_login(self, **kw):
        if not self._is_public_user():
            return request.redirect("/my/home")
        return request.redirect(self._login_redirect_url("/my/home?partner=1"))

    @http.route("/membership-sign-up", type="http", auth="public", website=True)
    def southern_membership_sign_up(self, **kw):
        return request.redirect(self._membership_product_url())

    @http.route("/partner-application", type="http", auth="public", website=True)
    def southern_partner_application(self, error=None, submitted=None, **kw):
        return request.render(
            "southern_customer_portal.southern_partner_application",
            {
                "error": error,
                "submitted": submitted,
                "business_types": request.env[
                    "southern.partner.application"
                ]._fields["business_type"].selection,
            },
        )

    @http.route(
        "/partner-application/submit",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
    )
    def southern_partner_application_submit(self, **post):
        required_fields = ["business_name", "contact_name", "email", "phone", "business_type"]
        if any(not (post.get(field) or "").strip() for field in required_fields):
            return self.southern_partner_application(error="missing")

        Application = request.env["southern.partner.application"].sudo()
        existing = Application.search(
            [
                ("email", "=ilike", post.get("email").strip()),
                ("state", "in", ["submitted", "approved", "active", "suspended"]),
            ],
            limit=1,
        )
        if existing:
            return self.southern_partner_application(submitted="existing")

        expected_monthly_spend = 0.0
        if post.get("expected_monthly_spend"):
            try:
                expected_monthly_spend = float(
                    post.get("expected_monthly_spend").replace(",", "")
                )
            except ValueError:
                return self.southern_partner_application(error="spend")

        Application.create(
            {
                "business_name": post.get("business_name").strip(),
                "contact_name": post.get("contact_name").strip(),
                "email": post.get("email").strip(),
                "phone": post.get("phone").strip(),
                "website": post.get("website", "").strip(),
                "business_type": post.get("business_type"),
                "expected_monthly_spend": expected_monthly_spend,
                "requested_terms": bool(post.get("requested_terms")),
                "tax_exempt": bool(post.get("tax_exempt")),
                "requested_catalog_access": True,
                "requested_partner_pricing": True,
                "portal_ip": request.httprequest.remote_addr,
                "notes": post.get("notes", "").strip(),
            }
        )
        return request.redirect("/partner-application?submitted=1")

    @http.route("/my/membership", type="http", auth="user", website=True)
    def portal_my_membership(self, error=None, **kw):
        values = self._prepare_portal_layout_values()
        values.update(
            {
                "application": self._get_membership_application(),
                "page_name": "membership",
                "error": error,
                "submitted": kw.get("submitted"),
            }
        )
        self._add_customer_access_values(values)
        return request.render("southern_customer_portal.portal_my_membership", values)

    @http.route("/my/membership/submit", type="http", auth="user", website=True, methods=["POST"])
    def portal_my_membership_submit(self, **post):
        required_fields = [
            "member_name",
            "phone",
            "email",
            "signature",
            "cardholder_name",
            "billing_street",
            "billing_zip",
            "billing_email",
            "billing_phone",
        ]
        if any(not (post.get(field) or "").strip() for field in required_fields):
            return self.portal_my_membership(error="missing")
        if not post.get("agreement_accepted") or not post.get("payment_authorized"):
            return self.portal_my_membership(error="acceptance")

        existing_application = self._get_membership_application()
        if existing_application and existing_application.state in ["submitted", "active", "suspended"]:
            return request.redirect("/my/membership")

        partner = request.env.user.partner_id.commercial_partner_id
        requested_house_credit = 2500.0
        if post.get("requested_house_credit"):
            try:
                requested_house_credit = float(post.get("requested_house_credit"))
            except ValueError:
                return self.portal_my_membership(error="credit")

        request.env["southern.membership.application"].sudo().create(
            {
                "partner_id": partner.id,
                "member_name": post.get("member_name").strip(),
                "phone": post.get("phone").strip(),
                "email": post.get("email").strip(),
                "signature": post.get("signature").strip(),
                "portal_ip": request.httprequest.remote_addr,
                "requested_house_credit": requested_house_credit,
                "cardholder_name": post.get("cardholder_name").strip(),
                "billing_street": post.get("billing_street").strip(),
                "billing_zip": post.get("billing_zip").strip(),
                "billing_email": post.get("billing_email").strip(),
                "billing_phone": post.get("billing_phone").strip(),
                "payment_authorized": True,
                "agreement_accepted": True,
                "notes": (
                    "Portal submission. Raw card numbers, expiration dates, and CVV "
                    "are intentionally not stored in Odoo."
                ),
            }
        )
        return request.redirect("/my/membership?submitted=1")

    @http.route(["/my/repair-orders", "/my/repair-orders/page/<int:page>"], type="http", auth="user", website=True)
    def portal_my_repair_orders(self, page=1, sortby="date", **kw):
        RepairOrder = request.env["repair.order"].sudo()
        values = self._prepare_portal_layout_values()
        self._add_customer_access_values(values)

        searchbar_sortings = {
            "date": {"label": "Newest", "order": "schedule_date desc, id desc"},
            "name": {"label": "Reference", "order": "name asc"},
            "status": {"label": "Status", "order": "state asc, schedule_date desc"},
        }
        sortby = sortby if sortby in searchbar_sortings else "date"
        domain = self._repair_order_domain()
        order_count = RepairOrder.search_count(domain)
        pager = portal_pager(
            url="/my/repair-orders",
            url_args={"sortby": sortby},
            total=order_count,
            page=page,
            step=self._items_per_page,
        )
        repair_orders = RepairOrder.search(
            domain,
            order=searchbar_sortings[sortby]["order"],
            limit=self._items_per_page,
            offset=pager["offset"],
        )

        values.update(
            {
                "repair_orders": repair_orders,
                "page_name": "repair_orders",
                "pager": pager,
                "default_url": "/my/repair-orders",
                "searchbar_sortings": searchbar_sortings,
                "sortby": sortby,
            }
        )
        return request.render("southern_customer_portal.portal_my_repair_orders", values)

    @http.route("/my/repair-orders/<int:order_id>", type="http", auth="user", website=True)
    def portal_my_repair_order(self, order_id, **kw):
        repair_order = request.env["repair.order"].sudo().search(
            [("id", "=", order_id)] + self._partner_domain(),
            limit=1,
        )
        if not repair_order:
            return request.not_found()
        values = self._prepare_portal_layout_values()
        self._add_customer_access_values(values)
        values.update(
            {
                "repair_order": repair_order,
                "page_name": "repair_order",
            }
        )
        return request.render("southern_customer_portal.portal_my_repair_order", values)

    @http.route(
        ["/my/outstanding-invoices", "/my/outstanding-invoices/page/<int:page>"],
        type="http",
        auth="user",
        website=True,
    )
    def portal_my_outstanding_invoices(self, page=1, sortby="due", **kw):
        Invoice = request.env["account.move"].sudo()
        values = self._prepare_portal_layout_values()
        self._add_customer_access_values(values)

        searchbar_sortings = {
            "due": {"label": "Due Date", "order": "invoice_date_due asc, invoice_date desc, id desc"},
            "date": {"label": "Invoice Date", "order": "invoice_date desc, id desc"},
            "amount": {"label": "Amount Due", "order": "amount_residual desc, invoice_date_due asc"},
        }
        sortby = sortby if sortby in searchbar_sortings else "due"
        domain = self._outstanding_invoice_domain()
        invoice_count = Invoice.search_count(domain)
        pager = portal_pager(
            url="/my/outstanding-invoices",
            url_args={"sortby": sortby},
            total=invoice_count,
            page=page,
            step=self._items_per_page,
        )
        invoices = Invoice.search(
            domain,
            order=searchbar_sortings[sortby]["order"],
            limit=self._items_per_page,
            offset=pager["offset"],
        )

        values.update(
            {
                "invoices": invoices,
                "page_name": "outstanding_invoices",
                "pager": pager,
                "default_url": "/my/outstanding-invoices",
                "searchbar_sortings": searchbar_sortings,
                "sortby": sortby,
            }
        )
        return request.render("southern_customer_portal.portal_my_outstanding_invoices", values)
