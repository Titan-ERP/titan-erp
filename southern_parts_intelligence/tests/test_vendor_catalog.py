import base64

from odoo.exceptions import UserError
from odoo.tests.common import TransactionCase

HASH = "a" * 64
PLAN_HASH = "b" * 64


class VendorCatalogTests(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.vendor = cls.env["res.partner"].create({"name": "Catalog Test Vendor", "supplier_rank": 1})
        cls.category = cls.env["product.category"].create({"name": "Catalog Test Pending"})
        cls.source = cls.env["southern.vendor.catalog.source"].create(
            {
                "name": "Catalog Test Vendor",
                "code": "catalog-test-vendor",
                "partner_id": cls.vendor.id,
                "source_type": "csv",
                "internal_reference_prefix": "CTV:",
                "default_category_id": cls.category.id,
                "promotion_batch_size": 100,
            }
        )
        cls.payload = {
            "vendor_sku": "A-100",
            "title": "Test Hydraulic Seal",
            "customer_description": "Replacement hydraulic seal.",
            "source_url": "https://vendor.example/products/a-100",
            "image_url": "https://vendor.example/images/a-100.jpg",
            "vendor_cost": 10,
            "sales_price": 20,
            "availability": "available",
        }

    def test_upsert_is_idempotent_and_builds_ready_queue(self):
        Item = self.env["southern.vendor.catalog.item"]
        first = Item.upsert_catalog_items(
            "catalog-test-vendor", [self.payload], "s3://catalog/test.jsonl", HASH
        )
        second = Item.upsert_catalog_items(
            "catalog-test-vendor", [self.payload], "s3://catalog/test.jsonl", HASH
        )
        item = Item.search([("source_id", "=", self.source.id), ("normalized_sku", "=", "A-100")])
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(len(item), 1)
        self.assertEqual(item.internal_reference, "CTV:A-100")
        self.assertEqual(item.promotion_state, "ready")
        self.assertFalse(item.blocker_code)
        self.assertEqual(item.observation_count, 2)

    def test_batch_match_is_case_insensitive_and_detects_duplicates(self):
        self.env["product.template"].create(
            {"name": "First staged duplicate", "default_code": "ctv:dup-1", "active": True}
        )
        self.env["product.template"].create(
            {"name": "Second staged duplicate", "default_code": "CTV:DUP-1", "active": False}
        )
        payloads = [
            {**self.payload, "vendor_sku": "DUP-1", "source_url": "https://vendor.example/products/dup-1"},
            {**self.payload, "vendor_sku": "NEW-1", "source_url": "https://vendor.example/products/new-1"},
        ]
        result = self.env["southern.vendor.catalog.item"].upsert_catalog_items(
            "catalog-test-vendor", payloads, "s3://catalog/batch-match.jsonl", HASH
        )
        items = self.env["southern.vendor.catalog.item"].search(
            [("source_id", "=", self.source.id), ("normalized_sku", "in", ["DUP-1", "NEW-1"])]
        )
        by_sku = {item.normalized_sku: item for item in items}
        self.assertEqual(result["observed"], 2)
        self.assertEqual(by_sku["DUP-1"].match_state, "duplicate")
        self.assertEqual(by_sku["DUP-1"].promotion_state, "blocked")
        self.assertEqual(by_sku["NEW-1"].match_state, "missing")

    def test_promotion_requires_source_opt_in_and_creates_unpublished_product(self):
        Item = self.env["southern.vendor.catalog.item"]
        Item.upsert_catalog_items("catalog-test-vendor", [self.payload], "s3://catalog/test.jsonl", HASH)
        item = Item.search([("source_id", "=", self.source.id), ("normalized_sku", "=", "A-100")])
        item.action_request_promotion()
        plan = Item.prepare_promotion_plan(item_ids=item.ids, limit=200)
        self.assertEqual(len(plan), 1)
        with self.assertRaises(UserError):
            Item.apply_promotion_plan(
                plan,
                "s3://catalog/promotion-plan.json",
                PLAN_HASH,
                "vendor-catalog-product-promotion",
                "Test promotion",
            )
        self.source.automatic_promotion_enabled = True
        plan = Item.prepare_promotion_plan(item_ids=item.ids, limit=200)
        promoted = Item.apply_promotion_plan(
            plan,
            "s3://catalog/promotion-plan.json",
            PLAN_HASH,
            "vendor-catalog-product-promotion",
            "Test promotion",
        )
        item.invalidate_recordset()
        self.assertEqual(len(promoted), 1)
        self.assertEqual(item.promotion_state, "promoted")
        self.assertEqual(item.product_id.default_code, "CTV:A-100")
        self.assertFalse(item.product_id.website_published)
        supplier = self.env["product.supplierinfo"].search([("product_tmpl_id", "=", item.product_id.id)])
        self.assertEqual(len(supplier), 1)
        self.assertEqual(supplier.partner_id, self.vendor)
        self.assertEqual(supplier.price, 10)

    def test_quote_only_plan_repairs_staged_fields_publishes_and_rolls_back(self):
        Item = self.env["southern.vendor.catalog.item"]
        website_category = self.env["product.public.category"].create({"name": "Quote Parts"})
        product = self.env["product.template"].create(
            {
                "name": "Quote Test Part",
                "default_code": "S.990001",
                "list_price": 1.0,
                "image_1920": base64.b64encode(b"quote-product-image"),
                "public_categ_ids": [(6, 0, website_category.ids)],
                "description_sale": "Internal catalog record. Not published to the website until pricing is reviewed.",
                "website_published": False,
            }
        )
        source = self.env["southern.vendor.catalog.source"].search(
            [("company_id", "=", self.env.company.id), ("code", "=", "sparex")], limit=1
        )
        if not source:
            source = self.env["southern.vendor.catalog.source"].create(
                {
                    "name": "Sparex",
                    "code": "sparex",
                    "partner_id": self.vendor.id,
                    "source_type": "web_listing",
                    "base_url": "https://us.sparex.com",
                    "default_category_id": self.category.id,
                }
            )
        Item.upsert_catalog_items(
            "sparex",
            [
                {
                    "vendor_sku": "S.990001",
                    "title": "Quote Test Part",
                    "source_url": "https://us.sparex.com/quote-test-part-990001.html",
                    "image_url": "https://cdn.example.com/quote-test-part.jpg",
                }
            ],
            "s3://catalog/sparex/quote-test.jsonl",
            HASH,
        )
        item = Item.search([("source_id", "=", source.id), ("normalized_sku", "=", "S.990001")])
        self.assertEqual(item.product_id, product)
        plan = Item.prepare_quote_publication_plan(limit=200)
        prepared = [record for record in plan if record["product_id"] == product.id]
        self.assertEqual(len(prepared), 1)
        applied = Item.apply_quote_publication_plan(
            prepared,
            "s3://catalog/sparex/quote-publication-plan.json",
            PLAN_HASH,
            "sparex-quote-only-publication",
            "Test quote-only publication",
        )
        product.invalidate_recordset()
        self.assertEqual(len(applied), 1)
        self.assertTrue(product.website_published)
        self.assertTrue(product.southern_quote_only)
        self.assertEqual(product.list_price, 0)
        self.assertEqual(product.southern_source_url, item.source_url)
        self.assertIn("current pricing", product.description_sale)
        Item.rollback_quote_publications(applied, "Test rollback")
        product.invalidate_recordset()
        self.assertFalse(product.website_published)
        self.assertFalse(product.southern_quote_only)
        self.assertEqual(product.list_price, 1.0)
