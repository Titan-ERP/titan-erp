import ast
import unittest
from pathlib import Path

from lxml import etree


ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "southern_service_operations"


def _manifest() -> dict:
    return ast.literal_eval((MODULE / "__manifest__.py").read_text(encoding="utf-8"))


class SouthernServiceOperationsTests(unittest.TestCase):
    def test_manifest_declares_app_and_required_dependencies(self):
        manifest = _manifest()
        self.assertIs(manifest["application"], True)
        self.assertEqual(manifest["name"], "Southern Service")
        self.assertEqual(manifest["author"], "Titan Equipment")
        self.assertTrue(
            {
                "sale_management",
                "industry_fsm",
                "repair",
                "maintenance",
                "purchase",
                "cs_client_equipment",
                "dmc_fieldservice",
            }.issubset(manifest["depends"])
        )

    def test_manifest_data_files_exist_and_xml_is_well_formed(self):
        for relative_path in _manifest()["data"]:
            path = MODULE / relative_path
            self.assertTrue(path.is_file(), relative_path)
            if path.suffix == ".xml":
                etree.parse(str(path))

    def test_sales_shortcuts_use_one_existing_list_header(self):
        document = etree.parse(str(MODULE / "views" / "sale_order_views.xml"))
        targets = document.xpath(
            "//record[@id='view_quotation_list_southern_service']"
            "/field[@name='arch']/xpath"
        )
        header_targets = [
            row for row in targets if row.get("expr") == "//list/header"
        ]
        self.assertEqual(len(header_targets), 1)
        self.assertEqual(
            [
                button.get("string")
                for button in header_targets[0].xpath("./button")
            ],
            ["Parts", "Service", "Equipment Sale", "Rental"],
        )

    def test_odoo_19_search_views_do_not_use_group_container(self):
        for path in (MODULE / "views").glob("*.xml"):
            document = etree.parse(str(path))
            self.assertFalse(
                document.xpath("//search/group"),
                f"{path.name} uses a removed Odoo 19 search/group container",
            )

    def test_odoo_19_model_constraints_use_constraint_descriptors(self):
        source = (MODULE / "models" / "service_case.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("_sql_constraints", source)
        self.assertIn("models.Constraint(", source)

    def test_sales_confirmation_creates_case_before_native_tasks(self):
        source = (MODULE / "models" / "sale_order.py").read_text(encoding="utf-8")
        method = source[source.index("    def action_confirm(self):") :]
        self.assertLess(
            method.index("_ensure_southern_service_case"),
            method.index("super().action_confirm()"),
        )

    def test_native_fsm_task_creation_receives_service_identity(self):
        source = (MODULE / "models" / "project_task.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            'vals["sale_line_id"]',
            '"southern_service_case_id"',
            '"southern_client_equipment_id"',
            '"dmc_equipment"',
            '"dmc_serial_number"',
            '"Unserialized"',
        ):
            self.assertIn(marker, source)

    def test_sales_reuses_an_unbilled_routed_task(self):
        source = (MODULE / "models" / "sale_order.py").read_text(encoding="utf-8")
        self.assertIn("def _timesheet_create_task(self, project):", source)
        self.assertIn("not task.sale_line_id", source)
        self.assertIn('"sale_line_id": self.id', source)
        self.assertIn("return existing_task", source)

    def test_service_users_work_from_sales(self):
        security = etree.parse(
            str(MODULE / "security" / "service_security.xml")
        )
        service_user = security.xpath(
            "//record[@id='group_southern_service_user']"
            "/field[@name='implied_ids']"
        )[0]
        self.assertIn("sales_team.group_sale_salesman", service_user.get("eval"))

        menus = etree.parse(str(MODULE / "views" / "service_menus.xml"))
        root = menus.xpath("//menuitem[@id='menu_southern_service_root']")[0]
        self.assertEqual(root.get("parent"), "sale.sale_menu_root")
        new_service = menus.xpath(
            "//menuitem[@id='menu_southern_service_new']"
        )[0]
        self.assertEqual(new_service.get("action"), "action_new_service_quotation")

    def test_sales_form_is_the_service_workspace(self):
        source = (MODULE / "models" / "sale_order.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def action_route_southern_service_work(self):", source)
        self.assertIn("def action_view_southern_service_tasks(self):", source)
        self.assertIn("def action_view_southern_service_repairs(self):", source)
        self.assertIn("def action_view_southern_service_purchases(self):", source)

        document = etree.parse(str(MODULE / "views" / "sale_order_views.xml"))
        self.assertEqual(
            len(
                document.xpath(
                    "//button[@name='action_route_southern_service_work']"
                )
            ),
            1,
        )
        self.assertEqual(
            len(document.xpath("//page[@name='southern_service_work']")),
            1,
        )

    def test_routing_is_idempotent_for_each_execution_model(self):
        source = (MODULE / "models" / "service_case.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("not case.task_ids", source)
        self.assertIn("not case.repair_order_ids", source)
        self.assertIn("not case.maintenance_request_ids", source)

    def test_unserialized_equipment_requires_explicit_flag(self):
        source = (MODULE / "models" / "client_equipment.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "not equipment.serial_no and not equipment.southern_unserialized",
            source,
        )


if __name__ == "__main__":
    unittest.main()
