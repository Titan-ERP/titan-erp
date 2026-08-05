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
