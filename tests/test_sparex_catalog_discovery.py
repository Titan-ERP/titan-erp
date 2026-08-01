import unittest
from pathlib import Path

from scripts.sparex_catalog_discovery import exact_sparex_product_url, parse_listing_page

ROOT = Path(__file__).resolve().parents[1]


class SparexCatalogDiscoveryParserTests(unittest.TestCase):
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

    def test_rejects_non_sparex_and_mismatched_product_urls(self):
        self.assertFalse(exact_sparex_product_url("https://example.com/filter-165551.html", "S.165551"))
        self.assertFalse(exact_sparex_product_url("https://us.sparex.com/filter-165552.html", "S.165551"))
        self.assertTrue(exact_sparex_product_url("https://us.sparex.com/filter-165551.html", "S.165551"))

    def test_worker_contract_forbids_product_mutation_and_detail_fetches(self):
        source = (ROOT / "scripts" / "sparex_catalog_discovery.py").read_text(encoding="utf-8")
        self.assertIn('WORKFLOW = "sparex-discovery-queue"', source)
        self.assertIn('product_creation_authorized": False', source)
        self.assertNotIn('client.call("product.template", "create"', source)
        self.assertNotIn('client.call("product.template", "write"', source)
        self.assertNotIn("session.get(source_url", source)
        self.assertIn("RequestThrottle(max(3.0, throttle_seconds))", source)


if __name__ == "__main__":
    unittest.main()
