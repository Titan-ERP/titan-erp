"""Improve Southern Equipment local SEO signals in Odoo.

Creates dedicated location pages, updates core page metadata, adds LocalBusiness
JSON-LD, and links the homepage/footer location blocks to the canonical pages.
"""

from __future__ import annotations

import html
import json
import os
import socket
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
WEBSITE_ID = 2
BASE_URL = "https://www.southernequipment.co"


LOCATIONS = {
    "laurel": {
        "page_name": "Southern Equipment Company - Laurel, MS",
        "url": "/locations/laurel-ms",
        "key": "website.southern_location_laurel_ms",
        "city": "Laurel",
        "state": "MS",
        "state_name": "Mississippi",
        "postal": "39443",
        "street": "5237 Hwy 84 West",
        "map_address": "5237 Hwy 84 West, Laurel, MS 39443",
        "phone": "+16016514555",
        "phone_display": "(601) 651-4555",
        "email": "info@southernequipment.co",
        "title": "Southern Equipment Company Laurel MS | Parts, Service, Rental",
        "description": (
            "Southern Equipment Company in Laurel, MS provides construction and agricultural equipment parts, "
            "service, rental support, and Titan equipment guidance."
        ),
        "lead": (
            "Parts, service, rental support, and Titan equipment guidance for contractors, farms, and fleets "
            "around Laurel and the South Central U.S."
        ),
        "directions": "https://www.google.com/maps/dir/?api=1&destination=5237%20Hwy%2084%20West%2C%20Laurel%2C%20MS%2039443",
        "services": [
            "Construction equipment parts",
            "Agricultural equipment parts",
            "Heavy equipment service",
            "Equipment rental support",
            "Titan equipment guidance",
            "Hydraulic, engine, filter, driveline, and ground-engaging parts",
        ],
        "area": "Laurel, Jones County, Hattiesburg, Waynesboro, and South Mississippi",
        "geo": {"latitude": 31.705, "longitude": -89.195},
        "same_as": [
            "https://www.facebook.com/p/Southern-Equipment-Company-100075869813582/",
            "https://www.bbb.org/us/ms/laurel/profile/heavy-duty-equipment-repair/southern-equipment-parts-llc-0523-235872432",
            "https://en-locator.engine.kubota.com/map/7846",
            "https://www.yanmar.com/us/dealerlocator/us/215073/",
        ],
    },
    "franklin": {
        "page_name": "Southern Equipment Company - Franklin, TX",
        "url": "/locations/franklin-tx",
        "key": "website.southern_location_franklin_tx",
        "city": "Franklin",
        "state": "TX",
        "state_name": "Texas",
        "postal": "77856",
        "street": "2188 US-79",
        "map_address": "2188 US-79, Franklin, TX 77856",
        "phone": "+18328081822",
        "phone_display": "(832) 808-1822",
        "email": "info@southernequipment.co",
        "title": "Southern Equipment Company Franklin TX | Equipment, Rental, Service",
        "description": (
            "Southern Equipment Company in Franklin, TX supports equipment, rental, parts, and service needs "
            "for contractors, farms, and fleets."
        ),
        "lead": (
            "Equipment, rental, parts, and service support for customers around Franklin, Robertson County, "
            "and Central Texas."
        ),
        "directions": "https://www.google.com/maps/dir/?api=1&destination=2188%20US-79%2C%20Franklin%2C%20TX%2077856",
        "services": [
            "Construction equipment support",
            "Agricultural equipment support",
            "Rental requests",
            "Parts sourcing",
            "Service request routing",
            "Titan equipment guidance",
        ],
        "area": "Franklin, Robertson County, Bryan, College Station, Hearne, and Central Texas",
        "geo": {"latitude": 31.029, "longitude": -96.485},
        "same_as": [
            "https://www.facebook.com/taskequipmentrental/",
        ],
    },
}


CORE_META = {
    "/": {
        "title": "Southern Equipment Company | Parts, Service, Rental, Titan Equipment",
        "description": (
            "Southern Equipment Company supports construction and agricultural equipment customers with parts, "
            "service, rentals, ecommerce, memberships, and Titan equipment in Laurel, MS and Franklin, TX."
        ),
    },
    "/rental": {
        "title": "Equipment Rental Support | Southern Equipment Company",
        "description": (
            "Request equipment rental support from Southern Equipment Company for construction, agriculture, "
            "lift, dirt work, material handling, attachments, terrain, and delivery needs."
        ),
    },
    "/service": {
        "title": "Heavy Equipment Service | Southern Equipment Company",
        "description": (
            "Southern Equipment Company provides shop and field service support for construction and agricultural "
            "equipment customers in Laurel, Franklin, and the South Central U.S."
        ),
    },
    "/request": {
        "title": "Request Parts, Service, Rental, or Equipment Help | Southern Equipment",
        "description": (
            "Send Southern Equipment Company a parts, service, rental, equipment, membership, or Titan support "
            "request and the team will route it to the right department."
        ),
    },
    "/shop": {
        "title": "Online Parts Store | Southern Equipment Company",
        "description": (
            "Shop Southern Equipment Company online for construction and agricultural equipment parts, filters, "
            "hydraulics, bearings, seals, hardware, driveline, lubricants, and more."
        ),
    },
}


def load_env() -> None:
    if not ENV_PATH.exists():
        raise SystemExit(f"Missing {ENV_PATH}.")
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    socket.setdefaulttimeout(120)
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def xmlid(models, db, uid, api_key, module: str, name: str) -> int:
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.model.data",
        "search_read",
        [[("module", "=", module), ("name", "=", name)]],
        {"fields": ["res_id"], "limit": 1},
    )
    if not rows:
        raise SystemExit(f"Missing XML ID {module}.{name}.")
    return rows[0]["res_id"]


def escape_json_for_xml(data: dict[str, Any]) -> str:
    return html.escape(json.dumps(data, indent=2, ensure_ascii=False), quote=False)


def local_business_schema(location: dict[str, Any]) -> dict[str, Any]:
    loc_id = f"{BASE_URL}{location['url']}#localbusiness"
    return {
        "@type": ["LocalBusiness", "Store"],
        "@id": loc_id,
        "name": f"Southern Equipment Company - {location['city']}, {location['state']}",
        "alternateName": [
            "Southern Equipment Company",
            "Southern Equipment & Parts",
            "Southern Equipment and Parts",
        ],
        "url": f"{BASE_URL}{location['url']}",
        "telephone": location["phone"],
        "email": location["email"],
        "priceRange": "$$",
        "image": f"{BASE_URL}/web/image/website/2/logo",
        "logo": f"{BASE_URL}/web/image/website/2/logo",
        "description": location["description"],
        "address": {
            "@type": "PostalAddress",
            "streetAddress": location["street"],
            "addressLocality": location["city"],
            "addressRegion": location["state"],
            "postalCode": location["postal"],
            "addressCountry": "US",
        },
        "geo": {
            "@type": "GeoCoordinates",
            "latitude": location["geo"]["latitude"],
            "longitude": location["geo"]["longitude"],
        },
        "openingHoursSpecification": [
            {
                "@type": "OpeningHoursSpecification",
                "dayOfWeek": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
                "opens": "07:00",
                "closes": "17:00",
            }
        ],
        "areaServed": location["area"],
        "hasMap": location["directions"],
        "sameAs": location["same_as"],
    }


def site_schema() -> dict[str, Any]:
    laurel = local_business_schema(LOCATIONS["laurel"])
    franklin = local_business_schema(LOCATIONS["franklin"])
    organization = {
        "@type": "Organization",
        "@id": f"{BASE_URL}/#organization",
        "name": "Southern Equipment Company",
        "alternateName": [
            "Southern Equipment & Parts LLC",
            "Southern Equipment and Parts",
            "Southern Equipment Company Laurel",
        ],
        "url": BASE_URL,
        "logo": f"{BASE_URL}/web/image/website/2/logo",
        "email": "info@southernequipment.co",
        "telephone": LOCATIONS["laurel"]["phone"],
        "sameAs": sorted(set(LOCATIONS["laurel"]["same_as"] + LOCATIONS["franklin"]["same_as"])),
        "department": [
            {"@id": laurel["@id"]},
            {"@id": franklin["@id"]},
        ],
    }
    website = {
        "@type": "WebSite",
        "@id": f"{BASE_URL}/#website",
        "name": "Southern Equipment Company",
        "url": BASE_URL,
        "publisher": {"@id": organization["@id"]},
        "potentialAction": {
            "@type": "SearchAction",
            "target": f"{BASE_URL}/website/search?search={{search_term_string}}",
            "query-input": "required name=search_term_string",
        },
    }
    return {"@context": "https://schema.org", "@graph": [organization, website, laurel, franklin]}


def location_page_arch(location: dict[str, Any]) -> str:
    services = "\n".join(f"<li>{html.escape(service)}</li>" for service in location["services"])
    schema = escape_json_for_xml({
        "@context": "https://schema.org",
        "@graph": [
            local_business_schema(location),
            {
                "@type": "BreadcrumbList",
                "itemListElement": [
                    {"@type": "ListItem", "position": 1, "name": "Home", "item": BASE_URL},
                    {
                        "@type": "ListItem",
                        "position": 2,
                        "name": f"{location['city']}, {location['state']}",
                        "item": f"{BASE_URL}{location['url']}",
                    },
                ],
            },
        ],
    })
    return f"""<t t-call="website.layout">
  <script type="application/ld+json">{schema}</script>
  <div id="wrap" class="oe_structure">
    <section class="pt80 pb72" style="background:#15171A;color:#fff;border-bottom:3px solid #EFBB2E;">
      <div class="container">
        <p style="color:#EFBB2E;font-weight:800;letter-spacing:.12em;text-transform:uppercase;">Southern Equipment Company</p>
        <h1 style="font-size:clamp(38px,5vw,68px);font-weight:900;line-height:1.02;margin:0 0 18px;">{html.escape(location['city'])}, {html.escape(location['state_name'])} Equipment Parts, Service, and Rental Support</h1>
        <p style="font-size:19px;line-height:1.65;color:#D8DDE2;max-width:780px;">{html.escape(location['lead'])}</p>
        <div class="d-flex flex-wrap gap-2 mt-4">
          <a class="btn btn-primary btn-lg" href="tel:{location['phone']}" style="background:#EFBB2E;border-color:#EFBB2E;color:#15171A;font-weight:900;">Call {html.escape(location['phone_display'])}</a>
          <a class="btn btn-outline-light btn-lg" href="/request">Request Help</a>
          <a class="btn btn-outline-light btn-lg" href="{html.escape(location['directions'])}" target="_blank" rel="noopener">Get Directions</a>
        </div>
      </div>
    </section>
    <section class="pt64 pb64">
      <div class="container">
        <div class="row g-4">
          <div class="col-lg-5">
            <div class="h-100 p-4" style="background:#fff;border:1px solid #D8D5CD;border-left:5px solid #EFBB2E;border-radius:8px;">
              <h2 style="font-weight:900;margin-bottom:18px;">Visit Southern Equipment in {html.escape(location['city'])}</h2>
              <p class="mb-2"><strong>Address</strong><br/>{html.escape(location['street'])}<br/>{html.escape(location['city'])}, {html.escape(location['state'])} {html.escape(location['postal'])}</p>
              <p class="mb-2"><strong>Phone</strong><br/><a href="tel:{location['phone']}">{html.escape(location['phone_display'])}</a></p>
              <p class="mb-2"><strong>Email</strong><br/><a href="mailto:{html.escape(location['email'])}">{html.escape(location['email'])}</a></p>
              <p class="mb-0"><strong>Hours</strong><br/>Monday to Friday, 7:00 a.m. to 5:00 p.m.</p>
            </div>
          </div>
          <div class="col-lg-7">
            <h2 style="font-weight:900;">What We Support</h2>
            <p style="font-size:17px;color:#5A6068;max-width:720px;">Southern Equipment helps customers source parts, schedule service support, request rental availability, and evaluate Titan equipment options.</p>
            <div class="row g-3 mt-2">
              <div class="col-md-6">
                <div class="p-4 h-100" style="background:#F5F4F1;border:1px solid #D8D5CD;border-radius:8px;">
                  <h3 style="font-size:22px;font-weight:900;">Services</h3>
                  <ul style="margin-bottom:0;line-height:1.8;">{services}</ul>
                </div>
              </div>
              <div class="col-md-6">
                <div class="p-4 h-100" style="background:#15171A;color:#fff;border-radius:8px;">
                  <h3 style="font-size:22px;font-weight:900;color:#fff;">Service Area</h3>
                  <p style="color:#D8DDE2;line-height:1.7;">{html.escape(location['area'])}</p>
                  <a class="btn btn-primary mt-2" href="/shop" style="background:#EFBB2E;border-color:#EFBB2E;color:#15171A;font-weight:900;">Shop Parts Online</a>
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  </div>
</t>"""


def upsert_page(models, db, uid, api_key, location: dict[str, Any]) -> int:
    values = {
        "name": location["page_name"],
        "key": location["key"],
        "type": "qweb",
        "mode": "primary",
        "priority": 16,
        "website_id": WEBSITE_ID,
        "active": True,
        "arch_db": location_page_arch(location),
        "website_meta_title": location["title"],
        "website_meta_description": location["description"],
        "website_meta_keywords": f"{location['city']} equipment parts, {location['city']} heavy equipment service, {location['city']} equipment rental",
    }
    pages = execute(
        models,
        db,
        uid,
        api_key,
        "website.page",
        "search_read",
        [[("website_id", "=", WEBSITE_ID), ("url", "=", location["url"])]],
        {"fields": ["id", "view_id"], "limit": 1},
    )
    if pages:
        page_id = pages[0]["id"]
        execute(models, db, uid, api_key, "website.page", "write", [[page_id], {**values, "url": location["url"], "is_published": True}])
        return page_id

    view_id = execute(models, db, uid, api_key, "ir.ui.view", "create", [values])
    page_id = execute(
        models,
        db,
        uid,
        api_key,
        "website.page",
        "create",
        [{
            "name": location["page_name"],
            "url": location["url"],
            "view_id": view_id,
            "website_id": WEBSITE_ID,
            "is_published": True,
        }],
    )
    return page_id


def upsert_schema_view(models, db, uid, api_key) -> int:
    layout_id = xmlid(models, db, uid, api_key, "website", "layout")
    schema = escape_json_for_xml(site_schema())
    arch = f"""<data inherit_id="website.layout" name="Southern Local Business Schema">
  <xpath expr="//head" position="inside">
    <script type="application/ld+json">{schema}</script>
  </xpath>
</data>"""
    values = {
        "name": "Southern Local Business Schema",
        "key": "website.southern_local_business_schema",
        "type": "qweb",
        "mode": "extension",
        "inherit_id": layout_id,
        "priority": 90,
        "website_id": WEBSITE_ID,
        "active": True,
        "arch_db": arch,
    }
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.ui.view",
        "search_read",
        [[("key", "=", values["key"]), ("website_id", "=", WEBSITE_ID)]],
        {"fields": ["id"], "limit": 1},
    )
    if rows:
        execute(models, db, uid, api_key, "ir.ui.view", "write", [[rows[0]["id"]], values])
        return rows[0]["id"]
    return execute(models, db, uid, api_key, "ir.ui.view", "create", [values])


def update_page_meta(models, db, uid, api_key) -> list[str]:
    updated = []
    for url, meta in CORE_META.items():
        pages = execute(
            models,
            db,
            uid,
            api_key,
            "website.page",
            "search_read",
            [[("website_id", "=", WEBSITE_ID), ("url", "=", url)]],
            {"fields": ["id", "website_meta_title", "website_meta_description"], "limit": 1},
        )
        if not pages:
            continue
        execute(
            models,
            db,
            uid,
            api_key,
            "website.page",
            "write",
            [[pages[0]["id"]], {
                "website_meta_title": meta["title"],
                "website_meta_description": meta["description"],
            }],
        )
        updated.append(url)
    return updated


def update_homepage_location_links(models, db, uid, api_key) -> bool:
    pages = execute(
        models,
        db,
        uid,
        api_key,
        "website.page",
        "search_read",
        [[("website_id", "=", WEBSITE_ID), ("url", "=", "/")]],
        {"fields": ["id", "arch_db"], "limit": 1},
    )
    if not pages:
        return False
    page = pages[0]
    arch = page["arch_db"]
    original = arch
    arch = arch.replace(
        '<a href="#locations" style="color:var(--gold)">See locations &amp; maps</a>',
        '<a href="/locations/laurel-ms" style="color:var(--gold)">Laurel location</a> &amp; '
        '<a href="/locations/franklin-tx" style="color:var(--gold)">Franklin location</a>',
    )
    arch = arch.replace(
        '<li><a href="#locations">Locations</a></li>',
        '<li><a href="#locations">Locations</a></li><li><a href="/locations/laurel-ms">Laurel, MS</a></li><li><a href="/locations/franklin-tx">Franklin, TX</a></li>',
    )
    if arch == original:
        return False
    execute(models, db, uid, api_key, "website.page", "write", [[page["id"]], {"arch_db": arch}])
    return True


def update_footer_location_links(models, db, uid, api_key) -> bool:
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.ui.view",
        "search_read",
        [[("key", "=", "website.footer_custom"), ("website_id", "=", WEBSITE_ID)]],
        {"fields": ["id", "arch_db"], "limit": 1},
    )
    if not rows:
        return False

    view = rows[0]
    arch = view["arch_db"]
    original = arch
    arch = arch.replace(
        '<p class="mb-1"><strong style="color:#EFBB2E;">Laurel, MS</strong></p>',
        '<p class="mb-1"><a href="/locations/laurel-ms" style="color:#EFBB2E;font-weight:900;text-decoration:none;">Laurel, MS</a></p>',
    )
    arch = arch.replace(
        '<p class="mb-3"><strong style="color:#EFBB2E;">Franklin, TX</strong></p>',
        '<p class="mb-3"><a href="/locations/franklin-tx" style="color:#EFBB2E;font-weight:900;text-decoration:none;">Franklin, TX</a></p>',
    )
    if arch == original:
        return False
    execute(models, db, uid, api_key, "ir.ui.view", "write", [[view["id"]], {"arch_db": arch}])
    return True


def main() -> int:
    db, uid, api_key, models = connect()
    schema_view_id = upsert_schema_view(models, db, uid, api_key)
    page_ids = {name: upsert_page(models, db, uid, api_key, location) for name, location in LOCATIONS.items()}
    meta_updated = update_page_meta(models, db, uid, api_key)
    homepage_links_updated = update_homepage_location_links(models, db, uid, api_key)
    footer_links_updated = update_footer_location_links(models, db, uid, api_key)
    print(json.dumps({
        "schema_view_id": schema_view_id,
        "location_page_ids": page_ids,
        "meta_updated": meta_updated,
        "homepage_links_updated": homepage_links_updated,
        "footer_links_updated": footer_links_updated,
        "location_urls": [f"{BASE_URL}{loc['url']}" for loc in LOCATIONS.values()],
    }, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
