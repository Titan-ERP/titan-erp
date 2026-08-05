from . import models


def post_init_hook(env_or_cr, registry=None):
    if registry:
        from odoo import api, SUPERUSER_ID

        env = api.Environment(env_or_cr, SUPERUSER_ID, {})
    else:
        env = env_or_cr
    companies = env["res.company"].search([("name", "ilike", "Southern Equipment")])
    Policy = env["southern.accounting.policy"]
    for company in companies:
        policy = Policy.search([("company_id", "=", company.id)], limit=1)
        if not policy:
            policy = Policy.create({"company_id": company.id})
        policy.action_fill_from_chart()
