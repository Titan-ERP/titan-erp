import json
import urllib.error
from datetime import UTC, datetime, timedelta

from scripts.sparex_catalog_agents.cost_recovery import (
    cooldown_active,
    parse_detail_image_url,
    parse_exact_priceb,
    write_cooldown,
)
from scripts.sparex_catalog_agents.orchestrator import verify_public_pages


def _page(container: str, title: str = "Exact Part") -> str:
    return f"<html><body><main><h1 itemprop='name'>{title}</h1>{container}</main></body></html>"


def test_accepts_only_one_price_in_exact_sku_container():
    parsed = parse_exact_priceb(_page("<div id='priceb_165551'><span>$14.99</span></div>"), "S.165551")
    assert parsed == {"status": "accepted", "price": 14.99, "currency": "USD"}


def test_rejects_generic_and_wrong_sku_prices():
    page = _page("<div>$9.99</div><div id='priceb_999999'>$14.99</div>")
    assert parse_exact_priceb(page, "S.165551")["status"] == "price_container_absent"


def test_accepts_one_magento_final_price_when_legacy_container_is_absent():
    page = _page('<script>{"final_price": 14.99}</script>')
    assert parse_exact_priceb(page, "S.165551") == {
        "status": "accepted",
        "price": 14.99,
        "currency": "USD",
    }


def test_rejects_conflicting_magento_final_prices():
    page = _page('<script>{"final_price": 14.99, "other": {"final_price": 12.50}}</script>')
    assert parse_exact_priceb(page, "S.165551")["status"] == "price_ambiguous"


def test_rejects_ambiguous_exact_container():
    page = _page("<div id='priceb_165551'><span>$14.99</span><span>$12.50</span></div>")
    assert parse_exact_priceb(page, "S.165551")["status"] == "price_ambiguous"


def test_requires_scoped_product_title():
    page = "<html><body><h1>Cookie dialog</h1><div id='priceb_165551'>$14.99</div></body></html>"
    assert parse_exact_priceb(page, "S.165551")["status"] == "identity_incomplete"


def test_extracts_one_canonical_detail_image_url():
    page = (
        "<html><head><meta property='og:image' content='/media/catalog/product/part.jpg'></head>"
        "<body><main><h1 itemprop='name'>Exact Part</h1></main></body></html>"
    )
    assert parse_detail_image_url(page, "https://us.sparex.com/exact-part-165551.html") == (
        "https://us.sparex.com/media/catalog/product/part.jpg"
    )


def test_rejects_ambiguous_detail_image_urls():
    page = (
        "<html><head><meta property='og:image' content='/one.jpg'></head><body><main>"
        "<img itemprop='image' src='/two.jpg'></main></body></html>"
    )
    assert not parse_detail_image_url(page, "https://us.sparex.com/exact-part-165551.html")


def test_portal_cooldown_is_persisted(tmp_path):
    write_cooldown(tmp_path, "portal_http_429")
    payload = json.loads((tmp_path / "dealer-cost-portal-cooldown.json").read_text(encoding="utf-8"))
    until = datetime.fromisoformat(payload["until_utc"])
    assert until > datetime.now(UTC) + timedelta(minutes=59)
    assert cooldown_active(tmp_path)


def test_public_verification_retries_during_storefront_propagation(monkeypatch):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        @staticmethod
        def read():
            return b"Published product S.165551"

    calls = iter([urllib.error.URLError("not ready"), Response()])

    def urlopen(*_args, **_kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    sleeps = []
    monkeypatch.setattr("scripts.sparex_catalog_agents.orchestrator.urllib.request.urlopen", urlopen)
    monkeypatch.setattr("scripts.sparex_catalog_agents.orchestrator.time.sleep", sleeps.append)

    result = verify_public_pages(
        "https://example.com",
        [{"task_id": 1, "product_id": 2, "sku": "S.165551", "public_path": "/shop/example"}],
    )

    assert result[0]["attempts"] == 2
    assert result[0]["http_status"] == 200
    assert sleeps == [2.0]
