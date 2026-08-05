import hashlib
import math
import re

from odoo import fields, http
from odoo.addons.portal.controllers.portal import pager as portal_pager
from odoo.http import request


PUBLIC_WEBSITE_STATUSES = [
    "needs_verification",
    "published",
    "inquiry_received",
    "verification_in_progress",
    "seller_confirmed",
    "under_negotiation",
    "under_contract",
]
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
BROKERAGE_COMPANY_NAME = "Southern Equipment Company (Laurel)"


class SouthernEquipmentBrokerageWebsite(http.Controller):
    PRIVACY_NOTICE_VERSION = "2026-07"

    def _is_brokerage_website(self):
        return request.website.company_id.name == BROKERAGE_COMPANY_NAME

    def _public_domain(self):
        return [
            ("company_id", "=", request.website.company_id.id),
            ("website_published", "=", True),
            ("public_status", "in", PUBLIC_WEBSITE_STATUSES),
        ]

    @http.route(
        ["/equipment-opportunities", "/equipment-opportunities/page/<int:page>"],
        type="http",
        auth="public",
        website=True,
        sitemap=True,
    )
    def equipment_opportunities(self, page=1, equipment_type=None, region=None, **kw):
        if not self._is_brokerage_website():
            return request.not_found()
        Listing = request.env["southern.equipment.listing"].sudo()
        domain = self._public_domain()
        if equipment_type:
            domain.append(("equipment_type", "=", equipment_type))
        if region:
            domain.append(("public_region", "=", region))
        total = Listing.search_count(domain)
        pager = portal_pager(
            url="/equipment-opportunities",
            url_args={"equipment_type": equipment_type, "region": region},
            total=total,
            page=page,
            step=9,
        )
        listings = Listing.search(
            domain,
            order="deal_score desc, create_date desc",
            limit=9,
            offset=pager["offset"],
        )
        regions = [
            public_region
            for (public_region,) in Listing._read_group(
                self._public_domain() + [("public_region", "!=", False)],
                groupby=["public_region"],
                order="public_region",
            )
        ]
        return request.render(
            "southern_equipment_brokerage.website_equipment_listings",
            {
                "listings": listings,
                "pager": pager,
                "equipment_type": equipment_type,
                "regions": regions,
                "region": region,
                "equipment_types": Listing._fields["equipment_type"].selection,
            },
        )

    @http.route(
        "/equipment-opportunities/<string:slug>",
        type="http",
        auth="public",
        website=True,
        sitemap=True,
    )
    def equipment_opportunity_detail(self, slug, submitted=None, error=None, **kw):
        if not self._is_brokerage_website():
            return request.not_found()
        listing = request.env["southern.equipment.listing"].sudo().search(
            self._public_domain() + [("public_slug", "=", slug)],
            limit=1,
        )
        if not listing:
            return request.not_found()
        return request.render(
            "southern_equipment_brokerage.website_equipment_listing_detail",
            {
                "listing": listing,
                "submitted": submitted,
                "error": error,
            },
        )

    def _find_broker(self, listing):
        if listing.broker_id:
            return listing.broker_id
        group = request.env.ref(
            "southern_equipment_brokerage.group_southern_deal_broker",
            raise_if_not_found=False,
        )
        return group.user_ids.filtered(lambda user: user.active)[:1] if group else False

    def _submission_fingerprint(self):
        remote_address = request.httprequest.remote_addr or "unknown"
        parameters = request.env["ir.config_parameter"].sudo()
        secret = (
            parameters.get_param("database.secret")
            or parameters.get_param("database.uuid")
            or request.db
        )
        return hashlib.sha256(
            f"{secret}:{remote_address}".encode("utf-8")
        ).hexdigest()

    def _privacy_notice_version(self):
        return (
            request.env["ir.config_parameter"]
            .sudo()
            .get_param(
                "southern_equipment_brokerage.privacy_notice_version",
                self.PRIVACY_NOTICE_VERSION,
            )
        )

    @http.route(
        "/privacy",
        type="http",
        auth="public",
        website=True,
        sitemap=True,
    )
    def privacy_notice(self, **kw):
        if not self._is_brokerage_website():
            return request.not_found()
        return request.render(
            "southern_equipment_brokerage.website_privacy_notice",
            {"privacy_notice_version": self._privacy_notice_version()},
        )

    @http.route(
        "/equipment-opportunities/<string:slug>/inquire",
        type="http",
        auth="public",
        website=True,
        methods=["POST"],
        csrf=True,
    )
    def equipment_opportunity_inquire(self, slug, **post):
        if not self._is_brokerage_website():
            return request.not_found()
        Listing = request.env["southern.equipment.listing"].sudo()
        listing = Listing.search(
            self._public_domain() + [("public_slug", "=", slug)],
            limit=1,
        )
        if not listing:
            return request.not_found()
        if post.get("website"):
            return request.redirect(f"{listing.website_url}?submitted=1")

        name = (post.get("name") or "").strip()[:120]
        email = (post.get("email") or "").strip()[:254]
        phone = (post.get("phone") or "").strip()[:60]
        if (
            not name
            or not EMAIL_PATTERN.fullmatch(email)
            or not phone
            or not re.search(r"\d", phone)
        ):
            return request.redirect(f"{listing.website_url}?error=missing#inquiry")
        if post.get("privacy_consent") not in ("1", "on", "true"):
            return request.redirect(f"{listing.website_url}?error=privacy#inquiry")

        budget = 0.0
        if post.get("budget"):
            try:
                budget = float(post["budget"])
                if not math.isfinite(budget) or budget < 0 or budget > 1_000_000_000:
                    raise ValueError
            except (TypeError, ValueError):
                return request.redirect(f"{listing.website_url}?error=budget#inquiry")

        timeline = post.get("timeline")
        if timeline not in ("immediate", "30_days", "90_days", "researching"):
            timeline = False
        broker = self._find_broker(listing)
        privacy_notice_version = self._privacy_notice_version()
        inquiry = request.env["southern.buyer.inquiry"].sudo().create_from_website(
            listing,
            {
                "contact_name": name,
                "phone": phone,
                "email": email,
                "company": (post.get("company") or "").strip()[:120],
                "buyer_location": (post.get("buyer_location") or "").strip()[:160],
                "budget": budget,
                "timeline": timeline,
                "financing_needed": bool(post.get("financing_needed")),
                "trade_in": bool(post.get("trade_in")),
                "message": (post.get("message") or "").strip()[:4000],
                "website_submission": True,
                "privacy_consent_at": fields.Datetime.now(),
                "privacy_notice_version": privacy_notice_version,
                "submission_fingerprint": self._submission_fingerprint(),
            },
            broker=broker,
        )
        if not inquiry:
            return request.redirect(f"{listing.website_url}?error=rate#inquiry")
        return request.redirect(f"{listing.website_url}?submitted=1#inquiry")
