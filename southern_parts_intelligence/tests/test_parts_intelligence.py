from odoo.tests.common import TransactionCase


class TestSouthernPartsIntelligence(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env["product.template"].create(
            {
                "name": "Sparex Test Fan Belt",
                "default_code": "S.TEST001",
                "barcode": "5023495000001",
                "list_price": 42.0,
                "southern_source_name": "Sparex",
                "southern_enrichment_status": "complete",
            }
        )
        cls.related_product = cls.env["product.template"].create(
            {
                "name": "Sparex Test Related Belt",
                "default_code": "S.TEST002",
                "list_price": 35.0,
            }
        )
        cls.make = cls.env["southern.parts.make"].create({"name": "Massey Ferguson"})
        cls.model = cls.env["southern.parts.model"].create({"name": "135", "make_id": cls.make.id})

        cls.env["southern.parts.specification"].create(
            {
                "product_tmpl_id": cls.product.id,
                "group_name": "Dimensions",
                "name": "Length",
                "value": "45",
                "unit": "in",
                "source_name": "Sparex",
            }
        )
        cls.env["southern.parts.fitment"].create(
            {
                "product_tmpl_id": cls.product.id,
                "make_id": cls.make.id,
                "model_id": cls.model.id,
                "engine": "Perkins",
                "build_list": "A4.236",
                "source_name": "Sparex",
            }
        )
        cls.env["southern.parts.oem_reference"].create(
            {
                "product_tmpl_id": cls.product.id,
                "manufacturer": "Massey Ferguson",
                "oem_part_number": "1447048M1",
                "reference_type": "oem",
                "source_name": "Sparex",
            }
        )
        cls.env["southern.parts.catalog_page"].create(
            {
                "product_tmpl_id": cls.product.id,
                "catalog_code": "S.61992",
                "catalog_name": "New and Fast Moving Book",
                "page_number": "105",
                "source_name": "Sparex",
            }
        )
        cls.env["southern.parts.related_product"].create(
            {
                "product_tmpl_id": cls.product.id,
                "related_product_tmpl_id": cls.related_product.id,
                "relationship_type": "alternate",
                "source_name": "Sparex",
            }
        )
        cls.env["southern.parts.alternate_barcode"].create(
            {
                "product_tmpl_id": cls.product.id,
                "barcode": "S.TEST001-ALT",
                "barcode_type": "supplier",
                "source_name": "Sparex",
            }
        )

    def test_search_text_aggregates_parts_counter_terms(self):
        self.product.invalidate_recordset(["southern_parts_search_text"])
        search_text = self.product.southern_parts_search_text

        self.assertIn("S.TEST001", search_text)
        self.assertIn("5023495000001", search_text)
        self.assertIn("Massey Ferguson", search_text)
        self.assertIn("135", search_text)
        self.assertIn("1447048M1", search_text)
        self.assertIn("S.61992", search_text)
        self.assertIn("S.TEST001-ALT", search_text)

    def test_website_catalog_sections_and_grouping(self):
        self.assertTrue(self.product._southern_has_parts_catalog_data())
        self.assertEqual(
            self.product._southern_website_catalog_sections(),
            ["specifications", "fitment", "oem", "catalog", "related"],
        )

        specs = self.product._southern_grouped_specifications()
        self.assertEqual(specs[0]["group"], "Dimensions")
        self.assertEqual(specs[0]["items"][0]["name"], "Length")

        fitments = self.product._southern_grouped_fitments()
        self.assertEqual(fitments[0]["make"], "Massey Ferguson")
        self.assertEqual(fitments[0]["items"][0]["model"], "135")

        refs = self.product._southern_grouped_oem_references()
        self.assertEqual(refs[0]["manufacturer"], "Massey Ferguson")
        self.assertIn("1447048M1", refs[0]["numbers"])

        catalogs = self.product._southern_grouped_catalog_pages()
        self.assertEqual(catalogs[0]["catalog"], "New and Fast Moving Book")
        self.assertEqual(catalogs[0]["pages"][0]["page_number"], "105")

    def test_quick_facts_include_catalog_summary(self):
        facts = self.product._southern_website_quick_facts()
        fact_map = {fact["label"]: fact["value"] for fact in facts}

        self.assertEqual(fact_map["SKU"], "S.TEST001")
        self.assertEqual(fact_map["Barcode"], "5023495000001")
        self.assertEqual(fact_map["OEM Cross References"], 1)
        self.assertEqual(fact_map["Fits"], "1 makes / 1 models")
        self.assertEqual(fact_map["Catalog References"], 1)

    def test_website_search_detail_includes_parts_search_text(self):
        website = self.env["website"].get_current_website()
        detail = self.product._search_get_detail(
            website,
            "name asc",
            {
                "displayDescription": True,
                "displayDetail": True,
                "displayExtraDetail": True,
                "displayExtraLink": True,
                "displayImage": True,
                "allowFuzzy": True,
                "category": None,
                "tags": None,
                "min_price": 0,
                "max_price": 0,
                "attribute_value_dict": {},
                "display_currency": website.currency_id,
            },
        )

        self.assertIn("southern_parts_search_text", detail["search_fields"])
        self.assertIn("southern_parts_search_text", detail["fetch_fields"])
        self.assertIn("southern_parts_search_text", detail["mapping"])
