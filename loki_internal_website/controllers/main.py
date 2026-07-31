from odoo import http
from odoo.http import request
from werkzeug.exceptions import NotFound


class LokiInternalWebsite(http.Controller):
    @http.route(
        "/loki",
        type="http",
        auth="user",
        website=True,
        sitemap=False,
        methods=["GET"],
    )
    def loki_dashboard(self, **_kwargs):
        user = request.env.user
        if not user.has_group("base.group_user"):
            raise NotFound()

        company = request.env.company
        lead_model = request.env["crm.lead"]
        company_domain = [("company_id", "=", company.id)]
        pipeline_counts = [
            {
                "label": stage_name,
                "count": lead_model.search_count(
                    company_domain + [("stage_id.name", "=", stage_name)]
                ),
            }
            for stage_name in ("Lead", "Suspect", "Prospect")
        ]

        parcel_model = request.env["loki.crm.parcel.link"]
        parcel_count = parcel_model.search_count(
            [("company_id", "=", company.id), ("active", "=", True)]
        )
        matched_parcel_count = parcel_model.search_count(
            [
                ("company_id", "=", company.id),
                ("active", "=", True),
                ("review_state", "=", "matched"),
            ]
        )

        crm_action = request.env.ref("crm.crm_lead_all_leads", raise_if_not_found=False)
        parcel_action = request.env.ref(
            "loki_crm_parcel_link.action_loki_crm_parcel_links",
            raise_if_not_found=False,
        )
        values = {
            "company": company,
            "pipeline_counts": pipeline_counts,
            "parcel_count": parcel_count,
            "matched_parcel_count": matched_parcel_count,
            "crm_url": f"/odoo/action-{crm_action.id}" if crm_action else "/odoo/crm",
            "parcel_url": (
                f"/odoo/action-{parcel_action.id}" if parcel_action else "/odoo"
            ),
        }
        return request.render("loki_internal_website.loki_internal_dashboard", values)
