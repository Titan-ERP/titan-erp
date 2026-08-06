import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.sparex_catalog_discovery import (
    RequestThrottle,
    _checked_request,
    _read_archived_json,
    adaptive_checkpoint_pages,
    exact_sparex_product_url,
    parse_listing_page,
)

ROOT = Path(__file__).resolve().parents[1]


class SparexCatalogDiscoveryParserTests(unittest.TestCase):
    def test_reads_checksum_verified_legacy_page_evidence_from_s3(self):
        content = json.dumps(
            {"schema_version": "1.1", "data": {"page_url": "https://us.sparex.com/products?p=2"}}
        ).encode()

        class Body:
            @staticmethod
            def read():
                return content

        class S3:
            @staticmethod
            def get_object(**kwargs):
                self.assertEqual(kwargs, {"Bucket": "catalog", "Key": "pages/2.json"})
                return {"Body": Body()}

        payload = _read_archived_json(
            "s3://catalog/pages/2.json", hashlib.sha256(content).hexdigest(), S3()
        )
        self.assertEqual(payload["page_url"], "https://us.sparex.com/products?p=2")
        with self.assertRaises(RuntimeError):
            _read_archived_json("s3://catalog/pages/2.json", "0" * 64, S3())

    def test_checked_request_records_request_latency_without_retries(self):
        class Session:
            @staticmethod
            def request(*_args, **_kwargs):
                return type("Response", (), {"status_code": 200, "raise_for_status": lambda self: None})()

        throttle = RequestThrottle(3.0)
        with patch(
            "scripts.sparex_catalog_discovery.time.monotonic",
            side_effect=[100.0, 100.0, 101.0, 110.5],
        ):
            _checked_request(Session(), throttle, "GET", "https://us.sparex.com/products")

        self.assertEqual(throttle.request_count, 1)
        self.assertEqual(throttle.slow_request_count, 1)
        self.assertEqual(throttle.max_request_seconds, 9.5)
        self.assertEqual(throttle.telemetry()["http_backoffs"], 0)

    def test_extracts_exact_links_images_and_next_listing_cursor(self):
        page = b"""
        <html><body>
          <ol class="products list items product-items">
            <li class="item product product-item" data-product-sku="S.165551">
              <a class="product-item-link" href="/oil-filter-spin-on-165551.html">Oil Filter S.165551</a>
              <img data-src="https://cdn.example.com/images/165551.jpg" />
            </li>
            <li class="item product product-item">
              <a class="product-item-link" href="https://us.sparex.com/filter-165552.html">S165552 Filter</a>
            </li>
          </ol>
          <li class="pages-item-next"><a href="/products?p=2">Next</a></li>
        </body></html>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/products?p=1")
        self.assertEqual([row["sku"] for row in parsed["items"]], ["S.165551", "S.165552"])
        self.assertEqual(parsed["items"][0]["listing_title"], "Oil Filter")
        self.assertEqual(parsed["items"][1]["listing_title"], "Filter")
        self.assertEqual(parsed["items"][0]["source_state"], "verified")
        self.assertEqual(parsed["items"][1]["source_state"], "missing_image")
        self.assertEqual(parsed["next_url"], "https://us.sparex.com/products?p=2")

    def test_builds_category_frontier_without_product_detail_or_cms_links(self):
        page = b"""
        <html><body>
          <nav>
            <a href="/engine-filters.html">Engine Filters</a>
            <a href="/hydraulics/couplings.html">Hydraulic Couplings</a>
            <a href="/help-page">Help</a>
            <a href="/search.html?q=filters">Search</a>
            <a href="https://example.com/external.html">External</a>
          </nav>
          <a href="/oil-filter-spin-on-165551.html">S.165551</a>
          <a href="/8530.html">S.8530</a>
        </body></html>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/")
        self.assertEqual(
            parsed["listing_urls"],
            [
                "https://us.sparex.com/engine-filters.html",
                "https://us.sparex.com/hydraulics/couplings.html",
            ],
        )

    def test_accepts_large_bounded_category_frontier(self):
        links = "".join(
            f'<a href="/category-{index}-parts.html">Category {index}</a>' for index in range(501)
        )
        parsed = parse_listing_page(
            f"<html><body>{links}</body></html>".encode(),
            "https://us.sparex.com/",
        )
        self.assertEqual(len(parsed["listing_urls"]), 501)

    def test_conflicting_explicit_card_sku_is_reviewed(self):
        page = b"""
        <div class="product-item" data-product-sku="S.999999">
          <a href="https://us.sparex.com/filter-165551.html">Wrong card S.999999</a>
          <img src="https://cdn.example.com/165551.jpg" />
        </div>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/products")
        self.assertEqual(parsed["items"][0]["sku"], "S.165551")
        self.assertEqual(parsed["items"][0]["source_state"], "ambiguous")

    def test_cross_reference_skus_in_title_do_not_override_url_identity(self):
        page = b"""
        <div class="product-item" data-product-sku="S.173096">
          <a href="/seal-kit-s-65503-s-159321-s-65172-173096.html">
            Seal Kit S.65503 S.159321 S.65172 S.173096
          </a>
          <img data-src="https://cdn.example.com/173096.jpg" />
        </div>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/products")
        self.assertEqual(parsed["items"][0]["sku"], "S.173096")
        self.assertEqual(parsed["items"][0]["source_state"], "verified")

    def test_ignores_script_style_and_placeholder_media(self):
        page = b"""
        <div class="product-item" data-product-sku="S.101023">
          <a href="/window-hinge-kit-side-and-rear-101023.html">
            <style>.product-image-container { width: 295px; }</style>
            <script>document.querySelector('.product-image-container');</script>
            <span>Window Hinge Kit Side and Rear S.101023</span>
          </a>
          <picture>
            <source data-srcset="https://cdn.example.com/101023.webp 1x" />
            <img src="https://us.sparex.com/media/catalog/product/placeholder/default/no-image.jpg" />
          </picture>
        </div>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/products")
        self.assertEqual(parsed["items"][0]["listing_title"], "Window Hinge Kit Side and Rear")
        self.assertEqual(parsed["items"][0]["image_url"], "https://cdn.example.com/101023.webp")
        self.assertEqual(parsed["items"][0]["source_state"], "verified")

    def test_placeholder_only_image_is_missing(self):
        page = b"""
        <div class="product-item" data-product-sku="S.101023">
          <a href="/window-hinge-kit-101023.html">Window Hinge Kit S.101023</a>
          <img src="https://us.sparex.com/media/catalog/product/placeholder/default/no-image.jpg" />
        </div>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/products")
        self.assertEqual(parsed["items"][0]["image_url"], "")
        self.assertEqual(parsed["items"][0]["source_state"], "missing_image")

    def test_uses_sparex_cdn_image_from_migrated_list_card(self):
        page = b"""
        <li class="item pm-listitem">
          <a href="/battery-hold-down-152774.html" class="product-image cdn-switch-img"
             data-cdnimg="https://cdn.example.com/imagelibrary_med/152774_pic1.jpg">
            <img src="https://us.sparex.com/media/catalog/product/placeholder/default/SJN1789_285x285.jpg" />
          </a>
          <h2 class="product-name">
            <a href="/battery-hold-down-152774.html">Battery Hold Down</a>
          </h2>
          <div class="product-icons"><img src="https://cdn.example.com/imagelibrary_itemicons/65.png" /></div>
        </li>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/products")
        self.assertEqual(parsed["items"][0]["listing_title"], "Battery Hold Down")
        self.assertEqual(
            parsed["items"][0]["image_url"],
            "https://cdn.example.com/imagelibrary_med/152774_pic1.jpg",
        )
        self.assertEqual(parsed["items"][0]["source_state"], "verified")

    def test_duplicate_same_product_anchor_keeps_image_backed_card_verified(self):
        page = b"""
        <li class="item pm-listitem">
          <a href="/engine-block-heater-156811.html">
            <img src="https://cdn.example.com/156811.jpg" />
            S.156811
          </a>
          <h2 class="product-name">
            <a href="/engine-block-heater-156811.html">Engine Block Heater</a>
          </h2>
        </li>
        """
        parsed = parse_listing_page(page, "https://us.sparex.com/products")
        self.assertEqual(len(parsed["items"]), 1)
        self.assertEqual(parsed["items"][0]["sku"], "S.156811")
        self.assertEqual(parsed["items"][0]["source_state"], "verified")
        self.assertEqual(parsed["items"][0]["image_url"], "https://cdn.example.com/156811.jpg")
        self.assertEqual(parsed["items"][0]["listing_title"], "Engine Block Heater")

    def test_rejects_non_sparex_and_mismatched_product_urls(self):
        self.assertFalse(exact_sparex_product_url("https://example.com/filter-165551.html", "S.165551"))
        self.assertFalse(exact_sparex_product_url("https://us.sparex.com/filter-165552.html", "S.165551"))
        self.assertTrue(exact_sparex_product_url("https://us.sparex.com/filter-165551.html", "S.165551"))

    def test_worker_contract_uses_odoo_creation_gate_and_forbids_detail_fetches(self):
        source = (ROOT / "scripts" / "sparex_catalog_discovery.py").read_text(encoding="utf-8")
        self.assertIn('WORKFLOW = "sparex-discovery-queue"', source)
        self.assertNotIn('client.call("product.template", "create"', source)
        self.assertNotIn('client.call("product.template", "write"', source)
        self.assertIn('"apply_product_creation_plan"', source)
        self.assertIn('parser.add_argument("--create-missing-products"', source)
        self.assertNotIn("session.get(source_url", source)
        self.assertIn("RequestThrottle(max(3.0, throttle_seconds))", source)
        self.assertIn('PARSER_VERSION = "sparex-listing-frontier-v6"', source)
        self.assertIn('kind="html"', source)

    def test_adaptive_checkpoint_expands_only_for_healthy_mature_runs(self):
        self.assertEqual(adaptive_checkpoint_pages(10, None), 5)
        self.assertEqual(
            adaptive_checkpoint_pages(
                10,
                {"page_count": 114, "recovery_state": "healthy", "consecutive_failure_count": 0},
            ),
            10,
        )
        self.assertEqual(
            adaptive_checkpoint_pages(
                10,
                {"page_count": 114, "recovery_state": "retrying", "consecutive_failure_count": 1},
            ),
            2,
        )


if __name__ == "__main__":
    unittest.main()
