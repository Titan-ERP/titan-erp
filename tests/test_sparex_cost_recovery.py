import json
from datetime import UTC, datetime, timedelta

from scripts.sparex_catalog_agents.cost_recovery import cooldown_active, parse_exact_priceb, write_cooldown


def _page(container: str, title: str = "Exact Part") -> str:
    return f"<html><body><main><h1 itemprop='name'>{title}</h1>{container}</main></body></html>"


def test_accepts_only_one_price_in_exact_sku_container():
    parsed = parse_exact_priceb(_page("<div id='priceb_165551'><span>$14.99</span></div>"), "S.165551")
    assert parsed == {"status": "accepted", "price": 14.99, "currency": "USD"}


def test_rejects_generic_and_wrong_sku_prices():
    page = _page("<div>$9.99</div><div id='priceb_999999'>$14.99</div>")
    assert parse_exact_priceb(page, "S.165551")["status"] == "price_container_absent"


def test_rejects_ambiguous_exact_container():
    page = _page("<div id='priceb_165551'><span>$14.99</span><span>$12.50</span></div>")
    assert parse_exact_priceb(page, "S.165551")["status"] == "price_ambiguous"


def test_requires_scoped_product_title():
    page = "<html><body><h1>Cookie dialog</h1><div id='priceb_165551'>$14.99</div></body></html>"
    assert parse_exact_priceb(page, "S.165551")["status"] == "identity_incomplete"


def test_portal_cooldown_is_persisted(tmp_path):
    write_cooldown(tmp_path, "portal_http_429")
    payload = json.loads((tmp_path / "dealer-cost-portal-cooldown.json").read_text(encoding="utf-8"))
    until = datetime.fromisoformat(payload["until_utc"])
    assert until > datetime.now(UTC) + timedelta(minutes=59)
    assert cooldown_active(tmp_path)
