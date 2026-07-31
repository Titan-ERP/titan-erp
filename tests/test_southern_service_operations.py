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
        self.assertEqual(manifest["author"], "Southern Equipment")
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

    def test_digital_inspection_result_fields_remain_editable(self):
        view_source = (
            MODULE / "views" / "service_inspection_views.xml"
        ).read_text(encoding="utf-8")
        self.assertIn('name="southern_equipment_inspection"', view_source)
        self.assertIn('<field name="result"/>', view_source)
        self.assertIn('<field name="priority" optional="show"/>', view_source)
        self.assertNotIn('<field name="result" widget="badge"/>', view_source)

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
            "dmc_equipment_run_hours",
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

        administrator = security.xpath(
            "//record[@id='base.group_system']"
            "/field[@name='implied_ids']"
        )[0]
        self.assertIn(
            "group_southern_service_manager", administrator.get("eval")
        )

        menus = etree.parse(str(MODULE / "views" / "service_menus.xml"))
        root = menus.xpath("//menuitem[@id='menu_southern_service_root']")[0]
        self.assertEqual(root.get("parent"), "sale.sale_menu_root")
        new_service = menus.xpath(
            "//menuitem[@id='menu_southern_service_new']"
        )[0]
        self.assertEqual(
            new_service.get("action"),
            "industry_fsm.project_task_action_fsm",
        )
        sales_views = etree.parse(str(MODULE / "views" / "sale_order_views.xml"))
        service_button = sales_views.xpath(
            "//button[@string='Service' and @type='action']"
        )[0]
        self.assertEqual(
            service_button.get("name"),
            "%(industry_fsm.project_task_action_fsm)d",
        )

    def test_sales_form_is_the_service_workspace(self):
        source = (MODULE / "models" / "sale_order.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("def action_route_southern_service_work(self):", source)
        self.assertIn("def action_view_southern_service_tasks(self):", source)
        self.assertIn("def action_view_southern_service_repairs(self):", source)
        self.assertIn("def action_view_southern_service_purchases(self):", source)
        for field_name in (
            "southern_technician_id",
            "southern_scheduled_start",
            "southern_estimated_hours",
            "southern_service_title",
            "southern_equipment_description",
            "southern_serial_number",
            "southern_equipment_run_hours",
        ):
            self.assertIn(field_name, source)
        self.assertNotIn("southern_equipment_exception_reason", source)

        document = etree.parse(str(MODULE / "views" / "sale_order_views.xml"))
        self.assertEqual(
            len(
                document.xpath(
                    "//button[@name='action_route_southern_service_work']"
                )
            ),
            1,
        )
        schedule_button = document.xpath(
            "//button[@name='action_route_southern_service_work']"
        )[0]
        self.assertEqual(
            schedule_button.get("string"), "Create / Update Service Job"
        )
        self.assertEqual(
            len(document.xpath("//page[@name='southern_service_work']")),
            1,
        )

    def test_native_field_service_is_presented_as_service_jobs(self):
        document = etree.parse(
            str(MODULE / "views" / "project_task_views.xml")
        )
        for action_id in (
            "industry_fsm.project_task_action_fsm",
            "industry_fsm.project_task_action_fsm2",
        ):
            name = document.xpath(
                f"//record[@id='{action_id}']/field[@name='name']/text()"
            )
            self.assertEqual(name, ["Service Jobs"])

        search_label = document.xpath(
            "//record[@id='view_task_search_southern_service_jobs']"
            "/field[@name='arch']/xpath"
            "/attribute[@name='string']/text()"
        )
        self.assertEqual(search_label, ["My Service Jobs"])

        sales_views = (MODULE / "views" / "sale_order_views.xml").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("Field Service task", sales_views)
        self.assertNotIn('string="Field Work"', sales_views)

    def test_service_job_form_uses_service_language_and_structured_notes(self):
        document = etree.parse(
            str(MODULE / "views" / "project_task_views.xml")
        )
        title = document.xpath(
            "//xpath[@expr=\"//field[@name='name']\"]"
            "/attribute[@name='placeholder']/text()"
        )
        self.assertEqual(title, ["Service Job Title..."])
        work_details = document.xpath(
            "//xpath[@expr=\"//page[@name='description_page']\"]"
            "/attribute[@name='string']/text()"
        )
        self.assertEqual(work_details, ["Work Details"])
        follow_up = document.xpath(
            "//xpath[@expr=\"//page[@name='sub_tasks_page']\"]"
            "/attribute[@name='string']/text()"
        )
        self.assertEqual(follow_up, ["Follow-up Work"])
        for field_name in (
            "southern_diagnosis",
            "southern_work_performed",
            "southern_recommendations",
        ):
            self.assertTrue(document.xpath(f"//field[@name='{field_name}']"))

        source = (MODULE / "models" / "project_task.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("_SOUTHERN_SERVICE_NOTE_MAP", source)
        self.assertIn("def write(self, vals):", source)

    def test_sales_schedule_maps_to_native_field_service_fields(self):
        source = (MODULE / "models" / "service_case.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('"user_ids":', source)
        self.assertIn('"planned_date_begin":', source)
        self.assertIn('"date_deadline":', source)
        self.assertIn('"allocated_hours":', source)
        self.assertIn('"dmc_equipment_run_hours":', source)
        self.assertIn("case.task_ids.write(task_values)", source)

    def test_service_tasks_feed_native_sales_quotation(self):
        source = (MODULE / "models" / "project_task.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            "southern_sale_order_id",
            "southern_quote_line_ids",
            "southern_service_work_item_ids",
            "southern_labor_work_item_ids",
            "def action_southern_create_quotation(self):",
            "def action_southern_add_labor_task(self):",
            "def action_southern_add_part(self):",
            "def action_southern_add_other_work(self):",
            "def action_southern_send_quotation(self):",
            "def action_southern_confirm_sale_order(self):",
        ):
            self.assertIn(marker, source)

        document = etree.parse(
            str(MODULE / "views" / "project_task_views.xml")
        )
        self.assertEqual(
            len(document.xpath("//page[@name='southern_quotation']")),
            1,
        )
        self.assertEqual(
            len(
                document.xpath(
                    "//field[@name='southern_labor_work_item_ids']"
                    "/list[@editable='bottom']"
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                document.xpath(
                    "//record[@id='view_southern_service_work_item_form']"
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                document.xpath(
                    "//button[@name='action_southern_add_part']"
                )
            ),
            0,
        )
        self.assertEqual(
            len(
                document.xpath(
                    "//separator[@string='Sales Quotation Lines']"
                )
            ),
            1,
        )
        work_item_source = (
            MODULE / "models" / "service_work_item.py"
        ).read_text(encoding="utf-8")
        for marker in (
            "allocated_hours",
            "quote_quantity",
            "sale_line_id",
            "def _sync_individual_to_quotation",
            '"product_uom_qty": self.quote_quantity',
        ):
            self.assertIn(marker, work_item_source)
        for marker in (
            "southern_labor_product_id",
            "southern_labor_sale_line_id",
            "def _southern_sync_tasks_to_quotation",
            "total_hours = sum(labor_items.mapped(\"allocated_hours\"))",
        ):
            self.assertIn(marker, source)

        sale_order_source = (
            MODULE / "models" / "sale_order.py"
        ).read_text(encoding="utf-8")
        self.assertIn("southern_service_task_id", sale_order_source)
        self.assertIn(
            "_onchange_southern_service_task_id",
            sale_order_source,
        )
        self.assertIn(
            '[("southern_sale_order_id", "=", order.id)]',
            sale_order_source,
        )

        self.assertEqual(
            len(
                document.xpath(
                    "//field[@name='southern_quote_line_ids']"
                    "/list[@editable='bottom']"
                    "/control/create[@string='Add a product']"
                )
            ),
            1,
        )
        self.assertEqual(
            len(
                document.xpath(
                    "//field[@name='southern_labor_work_item_ids']"
                    "/list/control/create"
                )
            ),
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
        self.assertIn("_southern_find_or_create_serialized", source)
        self.assertIn("_southern_ai_service_history", source)
        self.assertIn("_southern_ai_commercial_history", source)

    def test_ai_estimate_is_reviewed_before_native_quote_changes(self):
        source = (MODULE / "models" / "service_ai_suggestion.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            '"https://api.openai.com/v1/responses"',
            '"store": False',
            '"type": "json_schema"',
            '"type": "file_search"',
            "def action_southern_generate_ai_estimate(self):",
            "def action_apply_selected(self):",
            '"display_type": "line_note"',
            '"southern.service.work.item"',
            '"service_history":',
            '"photo_evidence":',
            '"completed_sales_and_invoices":',
        ):
            self.assertIn(marker, source)

    def test_service_photos_use_native_job_and_equipment_links(self):
        source = (MODULE / "models" / "service_inspection.py").read_text(
            encoding="utf-8"
        )
        for marker in (
            'class SouthernServicePhoto(models.Model):',
            '_name = "southern.service.photo"',
            'attachment=True',
            'southern_service_photo_ids',
            'def action_southern_add_service_photo(self):',
        ):
            self.assertIn(marker, source)
        document = etree.parse(
            str(MODULE / "views" / "service_inspection_views.xml")
        )
        self.assertEqual(
            len(
                document.xpath(
                    "//button[@name='action_southern_add_service_photo']"
                )
            ),
            1,
        )
        self.assertEqual(
            len(document.xpath("//field[@name='image'][@widget='image']")),
            2,
        )

        project_view = etree.parse(
            str(MODULE / "views" / "project_task_views.xml")
        )
        self.assertEqual(
            len(
                project_view.xpath(
                    "//button[@name='action_southern_generate_ai_estimate']"
                )
            ),
            1,
        )
        review_view = etree.parse(
            str(MODULE / "views" / "service_ai_suggestion_views.xml")
        )
        self.assertEqual(
            len(review_view.xpath("//button[@name='action_apply_selected']")),
            1,
        )
        self.assertEqual(
            len(review_view.xpath("//button[@name='action_reject']")),
            1,
        )


if __name__ == "__main__":
    unittest.main()
