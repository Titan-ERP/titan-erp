"""Turn the Southern membership CTA into a guided recurring-subscription checkout."""

import base64
import json
import os
import pathlib
import xmlrpc.client


ROOT = pathlib.Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / "odoo_connection.env"
MEMBERSHIP_VIEW_ID = 4883
MEMBERSHIP_TEMPLATE_ID = 25993
MEMBERSHIP_VARIANT_ID = 23735
MONTHLY_PLAN_ID = 3
SOUTHERN_WEBSITE_ID = 2
CTA_WRAPPER_VIEW_ID = 4143
QUANTITY_VIEW_ID = 4156


def load_env():
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


def connect():
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(
        db, username, api_key, {}
    )
    if not uid:
        raise RuntimeError("Odoo authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def main():
    load_env()
    db, uid, api_key, models = connect()

    product = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "read",
        [[MEMBERSHIP_TEMPLATE_ID]],
        {
            "fields": [
                "name",
                "type",
                "recurring_invoice",
                "subscription_rule_ids",
                "website_published",
            ]
        },
    )[0]
    if product["type"] != "service":
        raise RuntimeError("Membership must remain configured as a service.")
    if not product["recurring_invoice"]:
        raise RuntimeError("Membership is not configured as a subscription product.")
    if not product["subscription_rule_ids"]:
        raise RuntimeError("Membership has no recurring subscription pricing.")

    view = execute(
        models,
        db,
        uid,
        api_key,
        "ir.ui.view",
        "read",
        [[MEMBERSHIP_VIEW_ID]],
        {"fields": ["name", "arch_db"]},
    )[0]
    arch = view["arch_db"]

    old_hero_cta = (
        '<a class="btn btn-primary btn-lg" '
        'href="/shop/sec-membership-standard-standard-membership-25993" '
        'style="background:#EFBB2E;border-color:#EFBB2E;color:#17191C;font-weight:800;">'
        "Membership Sign Up</a>"
    )
    new_hero_cta = (
        '<a class="btn btn-primary btn-lg o_southern_membership_checkout" '
        'href="/shop/sec-membership-standard-standard-membership-25993" '
        'style="background:#EFBB2E;border-color:#EFBB2E;color:#17191C;font-weight:800;">'
        "Begin Secure Sign Up</a>"
    )
    if old_hero_cta in arch:
        arch = arch.replace(old_hero_cta, new_hero_cta, 1)
    elif "Begin Secure Sign Up" not in arch:
        raise RuntimeError("Could not find the membership hero signup CTA.")

    old_ready_block = """<h3>Ready to Join?</h3>
                                <p>Contact Southern Equipment to complete member acceptance, payment authorization, and any required credit approval.</p>
                                <a class="btn btn-primary" href="/contactus" style="background:#17191C;border-color:#17191C;">Contact Us</a>
                                <a class="btn btn-outline-secondary ms-2" href="tel:+16016514555">Call (601) 651-4555</a>"""
    new_ready_block = """<h3 id="signup">Secure Membership Sign Up</h3>
                                <p class="mb-3">Complete one guided process:</p>
                                <ol class="ps-3 mb-4">
                                    <li class="mb-2"><strong>Your information</strong> — name, phone, email, and billing address.</li>
                                    <li class="mb-2"><strong>Secure card entry</strong> — card details are collected by Stripe and are not stored on this page.</li>
                                    <li><strong>Monthly membership</strong> — Odoo starts the Monthly Membership subscription and recurring $25 invoices after checkout.</li>
                                </ol>
                                <a class="btn btn-primary o_southern_membership_checkout" href="/shop/sec-membership-standard-standard-membership-25993" style="background:#17191C;border-color:#17191C;">Continue to Secure Sign Up</a>
                                <a class="btn btn-outline-secondary ms-2" href="tel:+16016514555">Call (601) 651-4555</a>"""
    if old_ready_block in arch:
        arch = arch.replace(old_ready_block, new_ready_block, 1)
    elif "Secure Membership Sign Up" not in arch:
        raise RuntimeError("Could not find the membership Ready to Join block.")

    arch = arch.replace(
        '<a class="btn btn-primary btn-lg" '
        'href="/shop/sec-membership-standard-standard-membership-25993" '
        'style="background:#EFBB2E;border-color:#EFBB2E;color:#17191C;font-weight:800;">'
        "Begin Secure Sign Up</a>",
        new_hero_cta,
    )
    arch = arch.replace(
        '<a class="btn btn-primary" '
        'href="/shop/sec-membership-standard-standard-membership-25993" '
        'style="background:#17191C;border-color:#17191C;">'
        "Continue to Secure Sign Up</a>",
        '<a class="btn btn-primary o_southern_membership_checkout" '
        'href="/shop/sec-membership-standard-standard-membership-25993" '
        'style="background:#17191C;border-color:#17191C;">'
        "Continue to Secure Sign Up</a>",
    )
    arch = arch.replace(
        '<button type="button" class="btn btn-primary btn-lg" '
        'onclick="startSouthernMembershipSignup(this)" '
        'style="background:#EFBB2E;border-color:#EFBB2E;color:#17191C;font-weight:800;">'
        "Begin Secure Sign Up</button>",
        new_hero_cta,
    )
    arch = arch.replace(
        '<button type="button" class="btn btn-primary" '
        'onclick="startSouthernMembershipSignup(this)" '
        'style="background:#17191C;border-color:#17191C;">'
        "Start Membership Sign Up</button>",
        '<a class="btn btn-primary" '
        'href="/shop/sec-membership-standard-standard-membership-25993" '
        'style="background:#17191C;border-color:#17191C;">'
        "Continue to Secure Sign Up</a>",
    )
    script_start = arch.find(
        '\n            <script type="text/javascript">\n'
        "                function startSouthernMembershipSignup"
    )
    if script_start != -1:
        script_end = arch.find("</script>", script_start)
        if script_end == -1:
            raise RuntimeError("Could not safely remove the obsolete signup script.")
        arch = arch[:script_start] + arch[script_end + len("</script>") :]

    changed = arch != view["arch_db"]
    if changed:
        execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.view",
            "write",
            [[MEMBERSHIP_VIEW_ID], {"arch_db": arch}],
        )

    checkout_javascript = f"""(function () {{
    "use strict";
    if (window.__southernMembershipSignupLoaded) {{
        return;
    }}
    window.__southernMembershipSignupLoaded = true;
    document.addEventListener("click", async function (event) {{
        var button = event.target.closest(".o_southern_membership_checkout");
        if (!button) {{
            return;
        }}
        event.preventDefault();
        event.stopImmediatePropagation();
        var originalHtml = button.innerHTML;
        button.classList.add("disabled");
        button.setAttribute("aria-disabled", "true");
        button.innerHTML = '<i class="fa fa-spinner fa-spin me-2"></i>Opening your application...';
        try {{
            var response = await fetch("/shop/cart/add", {{
                method: "POST",
                credentials: "same-origin",
                headers: {{"Content-Type": "application/json"}},
                body: JSON.stringify({{
                    jsonrpc: "2.0",
                    method: "call",
                    params: {{
                        product_template_id: {MEMBERSHIP_TEMPLATE_ID},
                        product_id: {MEMBERSHIP_VARIANT_ID},
                        quantity: 1,
                        plan_id: {MONTHLY_PLAN_ID}
                    }},
                    id: Date.now()
                }})
            }});
            var payload = await response.json();
            if (!response.ok || payload.error) {{
                throw new Error("Unable to start membership checkout");
            }}
            window.location.assign("/shop/checkout");
        }} catch (error) {{
            button.classList.remove("disabled");
            button.removeAttribute("aria-disabled");
            button.innerHTML = originalHtml;
            window.location.assign("/shop/sec-membership-standard-standard-membership-25993");
        }}
    }}, true);
}})();
"""
    attachments = execute(
        models,
        db,
        uid,
        api_key,
        "ir.attachment",
        "search_read",
        [[
            ["name", "=", "southern_membership_signup.js"],
            ["res_model", "=", "ir.ui.view"],
            ["res_id", "=", MEMBERSHIP_VIEW_ID],
        ]],
        {"fields": ["id", "checksum"]},
    )
    attachment_values = {
        "name": "southern_membership_signup.js",
        "type": "binary",
        "mimetype": "application/javascript",
        "public": True,
        "res_model": "ir.ui.view",
        "res_id": MEMBERSHIP_VIEW_ID,
        "datas": base64.b64encode(checkout_javascript.encode()).decode(),
    }
    if attachments:
        attachment_id = attachments[0]["id"]
        execute(
            models,
            db,
            uid,
            api_key,
            "ir.attachment",
            "write",
            [[attachment_id], attachment_values],
        )
    else:
        attachment_id = execute(
            models,
            db,
            uid,
            api_key,
            "ir.attachment",
            "create",
            [attachment_values],
        )

    asset_path = f"/web/content/{attachment_id}/southern_membership_signup.js"
    assets = execute(
        models,
        db,
        uid,
        api_key,
        "ir.asset",
        "search_read",
        [[
            ["name", "=", "Southern Membership Direct Checkout"],
            ["website_id", "=", SOUTHERN_WEBSITE_ID],
        ]],
        {"fields": ["id"]},
    )
    asset_values = {
        "name": "Southern Membership Direct Checkout",
        "bundle": "web.assets_frontend",
        "directive": "append",
        "path": asset_path,
        "active": True,
        "sequence": 100,
        "website_id": SOUTHERN_WEBSITE_ID,
    }
    if assets:
        asset_id = assets[0]["id"]
        execute(
            models,
            db,
            uid,
            api_key,
            "ir.asset",
            "write",
            [[asset_id], asset_values],
        )
    else:
        asset_id = execute(
            models,
            db,
            uid,
            api_key,
            "ir.asset",
            "create",
            [asset_values],
        )

    buy_now_arch = f"""<data inherit_id="website_sale.cta_wrapper" name="Southern Membership Secure Checkout">
        <xpath expr="//div[@id='add_to_cart_wrap']" position="inside">
            <a t-if="product.id == {MEMBERSHIP_TEMPLATE_ID}"
               role="button"
               class="btn btn-primary o_southern_membership_checkout w-100 flex-grow-1"
               href="/shop/sec-membership-standard-standard-membership-25993">
                <i class="fa fa-lock me-2"/>
                Continue to Information &amp; Secure Payment
            </a>
            <script t-if="product.id == {MEMBERSHIP_TEMPLATE_ID}"
                    type="text/javascript"
                    src="/web/content/{attachment_id}/southern_membership_signup.js"/>
        </xpath>
    </data>"""
    buy_now_views = execute(
        models,
        db,
        uid,
        api_key,
        "ir.ui.view",
        "search_read",
        [[
            ["key", "=", "website.southern_membership_buy_now"],
            ["website_id", "=", SOUTHERN_WEBSITE_ID],
        ]],
        {"fields": ["id", "arch_db", "active"]},
    )
    buy_now_values = {
        "name": "Southern Membership Secure Checkout",
        "key": "website.southern_membership_buy_now",
        "type": "qweb",
        "mode": "extension",
        "inherit_id": CTA_WRAPPER_VIEW_ID,
        "priority": 30,
        "website_id": SOUTHERN_WEBSITE_ID,
        "active": True,
        "arch_db": buy_now_arch,
    }
    if buy_now_views:
        execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.view",
            "write",
            [[buy_now_views[0]["id"]], buy_now_values],
        )
        buy_now_view_id = buy_now_views[0]["id"]
    else:
        buy_now_view_id = execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.view",
            "create",
            [buy_now_values],
        )

    membership_cta_arch = f"""<data inherit_id="website_sale.cta_wrapper" name="Southern Membership Hide Cart CTA">
        <xpath expr="//a[@id='add_to_cart']" position="attributes">
            <attribute name="t-if">product.id != {MEMBERSHIP_TEMPLATE_ID}</attribute>
        </xpath>
    </data>"""
    cta_views = execute(
        models,
        db,
        uid,
        api_key,
        "ir.ui.view",
        "search_read",
        [[
            ["key", "=", "website.southern_membership_hide_cart_cta"],
            ["website_id", "=", SOUTHERN_WEBSITE_ID],
        ]],
        {"fields": ["id"]},
    )
    cta_values = {
        "name": "Southern Membership Hide Cart CTA",
        "key": "website.southern_membership_hide_cart_cta",
        "type": "qweb",
        "mode": "extension",
        "inherit_id": CTA_WRAPPER_VIEW_ID,
        "priority": 31,
        "website_id": SOUTHERN_WEBSITE_ID,
        "active": True,
        "arch_db": membership_cta_arch,
    }
    if cta_views:
        execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.view",
            "write",
            [[cta_views[0]["id"]], cta_values],
        )
        cta_view_id = cta_views[0]["id"]
    else:
        cta_view_id = execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.view",
            "create",
            [cta_values],
        )

    membership_quantity_arch = f"""<data inherit_id="website_sale.product_quantity" name="Southern Membership Hide Quantity">
        <xpath expr="//div[contains(@t-attf-class, 'css_quantity')]" position="attributes">
            <attribute name="t-if">product.id != {MEMBERSHIP_TEMPLATE_ID}</attribute>
        </xpath>
    </data>"""
    quantity_views = execute(
        models,
        db,
        uid,
        api_key,
        "ir.ui.view",
        "search_read",
        [[
            ["key", "=", "website.southern_membership_hide_quantity"],
            ["website_id", "=", SOUTHERN_WEBSITE_ID],
        ]],
        {"fields": ["id"]},
    )
    quantity_values = {
        "name": "Southern Membership Hide Quantity",
        "key": "website.southern_membership_hide_quantity",
        "type": "qweb",
        "mode": "extension",
        "inherit_id": QUANTITY_VIEW_ID,
        "priority": 31,
        "website_id": SOUTHERN_WEBSITE_ID,
        "active": True,
        "arch_db": membership_quantity_arch,
    }
    if quantity_views:
        execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.view",
            "write",
            [[quantity_views[0]["id"]], quantity_values],
        )
        quantity_view_id = quantity_views[0]["id"]
    else:
        quantity_view_id = execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.view",
            "create",
            [quantity_values],
        )

    rule = execute(
        models,
        db,
        uid,
        api_key,
        "product.pricelist.item",
        "read",
        [product["subscription_rule_ids"]],
        {"fields": ["fixed_price", "plan_id", "product_tmpl_id", "company_id"]},
    )
    print(
        json.dumps(
            {
                "view_updated": changed,
                "website_checkout_views": {
                    "buy_now": buy_now_view_id,
                    "hide_cart": cta_view_id,
                    "hide_quantity": quantity_view_id,
                },
                "checkout_javascript_attachment": attachment_id,
                "checkout_frontend_asset": asset_id,
                "product": product,
                "subscription_pricing": rule,
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
