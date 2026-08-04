import ast
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "southern_customer_portal"


class SynchronyContactAccountTests(unittest.TestCase):
    def test_partner_has_tracked_synchrony_account_number(self):
        source = (MODULE / "models" / "res_partner.py").read_text(encoding="utf-8")
        self.assertIn("southern_synchrony_account_number = fields.Char(", source)
        self.assertIn('string="Synchrony Account Number"', source)
        self.assertIn("copy=False", source)
        self.assertIn("tracking=True", source)

    def test_contact_form_has_financing_section(self):
        path = MODULE / "views" / "res_partner_views.xml"
        root = ET.parse(path).getroot()
        financing_page = root.find(".//page[@name='southern_customer_financing']")
        self.assertIsNotNone(financing_page)
        self.assertEqual(financing_page.get("string"), "Financing")
        self.assertIsNotNone(
            financing_page.find(".//field[@name='southern_synchrony_account_number']")
        )

    def test_module_version_is_bumped(self):
        manifest = ast.literal_eval((MODULE / "__manifest__.py").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "19.0.1.0.21")


if __name__ == "__main__":
    unittest.main()
