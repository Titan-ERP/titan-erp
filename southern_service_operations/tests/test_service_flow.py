from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged("at_install", "-post_install")
class TestSouthernServiceFlow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.partner = cls.env["res.partner"].create(
            {
                "name": "Southern Service Test Customer",
                "email": "service-test@example.com",
            }
        )
        cls.fsm_project = cls.env["project.project"].create(
            {
                "name": "Southern Service Test Project",
                "is_fsm": True,
                "partner_id": cls.partner.id,
                "company_id": cls.env.company.id,
            }
        )
        cls.service_product = cls.env["product.product"].create(
            {
                "name": "Southern Service Test Labor",
                "type": "service",
                "service_tracking": "task_global_project",
                "project_id": cls.fsm_project.id,
                "list_price": 125.0,
            }
        )
        cls.part_product = cls.env["product.product"].create(
            {
                "name": "Southern Service Test Filter",
                "type": "consu",
                "list_price": 42.50,
            }
        )
        cls.equipment = cls.env["equipment.details"].create(
            {
                "name": "Southern Service Test Equipment",
                "client": cls.partner.id,
                "serial_no": "SOUTHERN-SERVICE-TEST-001",
                "product_id": cls.service_product.id,
            }
        )

    def _create_customer_case(self):
        return self.env["southern.service.case"].create(
            {
                "service_domain": "customer",
                "partner_id": self.partner.id,
                "client_equipment_id": self.equipment.id,
                "service_location": "onsite",
                "service_title": "Diagnose test equipment",
                "equipment_description": self.equipment.name,
                "serial_number": self.equipment.serial_no,
                "equipment_run_hours": 2450,
                "complaint": "Development workflow test",
                "diagnosis": "Hydraulic pressure below specification",
                "work_performed": "Inspected pump and adjusted relief valve",
                "recommendations": "Recheck pressure after 50 operating hours",
            }
        )

    def test_onsite_routing_is_idempotent(self):
        case = self._create_customer_case()
        case.action_route_work()
        self.assertEqual(len(case.task_ids), 1)
        task = case.task_ids
        self.assertEqual(task.partner_id, self.partner)
        self.assertEqual(task.southern_client_equipment_id, self.equipment)
        self.assertEqual(task.dmc_equipment, self.equipment.name)
        self.assertEqual(task.dmc_serial_number, self.equipment.serial_no)
        self.assertEqual(task.dmc_equipment_run_hours, 2450)
        self.assertEqual(task.southern_diagnosis, case.diagnosis)
        self.assertEqual(task.southern_work_performed, case.work_performed)
        self.assertEqual(task.southern_recommendations, case.recommendations)

        task.write(
            {
                "southern_diagnosis": "Relief valve out of adjustment",
                "southern_work_performed": "Reset valve and verified pressure",
                "southern_recommendations": "No immediate follow-up required",
            }
        )
        self.assertEqual(case.diagnosis, "Relief valve out of adjustment")
        self.assertEqual(
            case.work_performed,
            "Reset valve and verified pressure",
        )
        self.assertEqual(
            case.recommendations,
            "No immediate follow-up required",
        )

        case.action_route_work()
        self.assertEqual(case.task_ids, task)

    def test_serial_intake_creates_contact_equipment_and_reuses_history(self):
        serial_number = "SOUTHERN-AUTO-EQUIPMENT-001"
        first_case = self.env["southern.service.case"].create(
            {
                "service_domain": "customer",
                "partner_id": self.partner.id,
                "service_location": "onsite",
                "service_title": "First hydraulic service",
                "equipment_description": "Southern Test Excavator",
                "serial_number": serial_number,
                "equipment_run_hours": 1200,
                "complaint": "Hydraulics lose power when warm",
                "diagnosis": "Return filter restricted",
                "work_performed": "Replaced return filter",
                "recommendations": "Recheck after 50 hours",
            }
        )
        equipment = first_case.client_equipment_id
        self.assertTrue(equipment)
        self.assertEqual(equipment.client, self.partner)
        self.assertEqual(equipment.serial_no, serial_number)

        second_case = self.env["southern.service.case"].create(
            {
                "service_domain": "customer",
                "partner_id": self.partner.id,
                "service_location": "onsite",
                "service_title": "Follow-up hydraulic service",
                "equipment_description": "Southern Test Excavator",
                "serial_number": serial_number,
                "equipment_run_hours": 1260,
                "complaint": "Hydraulic performance follow-up",
            }
        )
        self.assertEqual(second_case.client_equipment_id, equipment)
        second_case.action_route_work()
        service_history = second_case.task_ids._southern_ai_input()[
            "equipment_record"
        ]["service_history"]
        self.assertEqual(len(service_history), 1)
        self.assertEqual(service_history[0]["record"], first_case.name)
        self.assertEqual(service_history[0]["run_hours"], 1200)
        self.assertIn("Return filter", service_history[0]["diagnosis"])

        other_customer = self.env["res.partner"].create(
            {"name": "Other Southern Service Customer"}
        )
        with self.assertRaises(ValidationError):
            self.env["southern.service.case"].create(
                {
                    "service_domain": "customer",
                    "partner_id": other_customer.id,
                    "service_location": "onsite",
                    "service_title": "Incorrect customer intake",
                    "equipment_description": "Southern Test Excavator",
                    "serial_number": serial_number.lower(),
                    "complaint": "This serial belongs to another customer",
                }
            )

    def test_ai_uses_completed_equipment_sales_and_excludes_drafts(self):
        completed_order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "southern_quote_type": "service",
                "southern_service_location": "onsite",
                "southern_client_equipment_id": self.equipment.id,
                "southern_service_title": "Completed hydraulic service",
                "southern_equipment_description": self.equipment.name,
                "southern_serial_number": self.equipment.serial_no,
                "southern_service_request": "Replace filters and inspect pump",
                "southern_estimated_hours": 3.0,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.part_product.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )
        completed_order.write({"state": "sale"})
        draft_order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "southern_quote_type": "service",
                "southern_service_location": "onsite",
                "southern_client_equipment_id": self.equipment.id,
                "southern_service_title": "Abandoned estimate",
                "southern_equipment_description": self.equipment.name,
                "southern_serial_number": self.equipment.serial_no,
                "southern_service_request": "Draft only",
            }
        )

        commercial_history = self.equipment._southern_ai_commercial_history()
        self.assertEqual(
            commercial_history["lifetime_summary"][
                "completed_service_order_count"
            ],
            1,
        )
        recent_orders = commercial_history["recent_orders"]
        self.assertEqual(len(recent_orders), 1)
        self.assertEqual(
            recent_orders[0]["sales_order"],
            completed_order.name,
        )
        self.assertEqual(recent_orders[0]["estimated_hours"], 3.0)
        self.assertEqual(recent_orders[0]["products"][0]["quantity"], 2.0)
        self.assertEqual(
            commercial_history["lifetime_summary"]["product_usage"][0][
                "ordered_quantity"
            ],
            2.0,
        )
        self.assertNotEqual(recent_orders[0]["sales_order"], draft_order.name)

    def test_confirmed_service_sale_reuses_routed_task(self):
        case = self._create_customer_case()
        case.action_route_work()
        routed_task = case.task_ids
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "southern_quote_type": "service",
                "southern_service_location": "onsite",
                "southern_client_equipment_id": self.equipment.id,
                "southern_service_title": "Diagnose test equipment",
                "southern_equipment_description": self.equipment.name,
                "southern_serial_number": self.equipment.serial_no,
                "southern_equipment_run_hours": 2450,
                "southern_service_request": "Development workflow test",
                "southern_service_case_id": case.id,
                "order_line": [
                    Command.create(
                        {
                            "product_id": self.service_product.id,
                            "product_uom_qty": 2.0,
                        }
                    )
                ],
            }
        )
        case.sale_order_id = order

        order.action_confirm()

        self.assertEqual(order.order_line.task_id, routed_task)
        self.assertEqual(case.task_ids, routed_task)
        self.assertEqual(routed_task.sale_order_id, order)
        self.assertEqual(routed_task.sale_line_id, order.order_line)

    def test_sales_service_workspace_routes_one_contextual_job(self):
        scheduled_start = fields.Datetime.to_datetime("2026-08-03 14:00:00")
        order = self.env["sale.order"].create(
            {
                "partner_id": self.partner.id,
                "southern_quote_type": "service",
                "southern_service_location": "onsite",
                "southern_client_equipment_id": self.equipment.id,
                "southern_service_title": "Technician diagnosis",
                "southern_equipment_description": self.equipment.name,
                "southern_serial_number": self.equipment.serial_no,
                "southern_equipment_run_hours": 3512,
                "southern_service_request": "Technician diagnosis and estimate",
                "southern_commercial_basis": "estimate",
                "southern_technician_id": self.env.user.id,
                "southern_scheduled_start": scheduled_start,
                "southern_estimated_hours": 3.5,
            }
        )

        action = order.action_route_southern_service_work()
        case = order.southern_service_case_id
        self.assertTrue(case)
        self.assertEqual(order.partner_id, self.partner)
        self.assertEqual(order.southern_quote_type, "service")
        self.assertEqual(order.southern_client_equipment_id, self.equipment)
        self.assertEqual(case.sale_order_id, order)
        self.assertEqual(case.complaint, order.southern_service_request)
        self.assertEqual(case.state, "scheduled")
        self.assertEqual(case.task_count, 1)
        self.assertEqual(case.task_ids.partner_id, self.partner)
        self.assertEqual(case.task_ids.user_ids, self.env.user)
        self.assertEqual(case.task_ids.planned_date_begin, scheduled_start)
        self.assertEqual(case.task_ids.allocated_hours, 3.5)
        self.assertEqual(case.task_ids.dmc_equipment, self.equipment.name)
        self.assertEqual(case.task_ids.dmc_serial_number, self.equipment.serial_no)
        self.assertEqual(case.task_ids.dmc_equipment_run_hours, 3512)
        self.assertEqual(action, {"type": "ir.actions.client", "tag": "reload"})

        updated_start = fields.Datetime.to_datetime("2026-08-04 15:30:00")
        order.write(
            {
                "southern_scheduled_start": updated_start,
                "southern_estimated_hours": 4.0,
            }
        )
        order.action_route_southern_service_work()
        self.assertEqual(case.task_count, 1)
        self.assertEqual(case.task_ids.planned_date_begin, updated_start)
        self.assertEqual(case.task_ids.allocated_hours, 4.0)

    def test_field_service_job_builds_one_editable_sales_quotation(self):
        case = self._create_customer_case()
        case.action_route_work()
        task = case.task_ids

        action = task.action_southern_create_quotation()
        order = task.southern_sale_order_id
        self.assertTrue(order)
        self.assertEqual(case.sale_order_id, order)
        self.assertEqual(order.partner_id, self.partner)
        self.assertEqual(order.southern_quote_type, "service")
        self.assertEqual(
            order.southern_equipment_description,
            self.equipment.name,
        )
        self.assertEqual(order.southern_serial_number, self.equipment.serial_no)
        self.assertEqual(order.southern_equipment_run_hours, 2450)
        self.assertEqual(action, {"type": "ir.actions.client", "tag": "reload"})

        line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.service_product.id,
                "product_uom_qty": 2.0,
            }
        )
        self.assertEqual(task.southern_quote_line_ids, line)

        task.action_southern_create_quotation()
        self.assertEqual(task.southern_sale_order_id, order)

    def test_service_tasks_feed_quote_and_allocated_time(self):
        case = self._create_customer_case()
        case.action_route_work()
        task = case.task_ids
        task.write(
            {
                "southern_quote_workflow": True,
                "southern_labor_product_id": self.service_product.id,
            }
        )
        self.assertEqual(task.southern_labor_rate, 125.0)
        labor = self.env["southern.service.work.item"].create(
            {
                "task_id": task.id,
                "name": "Diagnose hydraulic system",
                "work_type": "labor",
                "assigned_user_id": self.env.user.id,
                "allocated_hours": 3.0,
            }
        )
        second_labor = self.env["southern.service.work.item"].create(
            {
                "task_id": task.id,
                "name": "Test hydraulic pressure",
                "work_type": "labor",
                "assigned_user_id": self.env.user.id,
                "allocated_hours": 1.5,
            }
        )
        nonbillable = self.env["southern.service.work.item"].create(
            {
                "task_id": task.id,
                "name": "Internal safety review",
                "work_type": "labor",
                "allocated_hours": 0.5,
                "billable": False,
            }
        )
        part = self.env["southern.service.work.item"].create(
            {
                "task_id": task.id,
                "name": "Replace hydraulic filter",
                "work_type": "part",
                "product_id": self.part_product.id,
                "quantity": 2.0,
            }
        )

        task.action_southern_create_quotation()
        order = task.southern_sale_order_id
        self.env.flush_all()
        task.invalidate_recordset(["southern_labor_sale_line_id"])
        order.invalidate_recordset(["order_line"])
        labor_line = task.southern_labor_sale_line_id
        quoted_labor_lines = order.order_line.filtered(
            lambda line: line.product_id == self.service_product
            and line.name.startswith("Service Labor")
        )
        self.assertEqual(task.southern_service_task_hours, 5.0)
        self.assertEqual(
            task.southern_labor_work_item_ids,
            labor | second_labor | nonbillable,
        )
        self.assertNotIn(part, task.southern_labor_work_item_ids)
        self.assertEqual(task.allocated_hours, 5.0)
        self.assertEqual(order.southern_estimated_hours, 5.0)
        self.assertEqual(case.estimated_hours, 5.0)
        self.assertEqual(quoted_labor_lines, labor_line)
        self.assertEqual(labor_line.order_id, order)
        self.assertEqual(labor_line.product_uom_qty, 4.5)
        self.assertEqual(labor_line.price_unit, 125.0)
        self.assertEqual(labor_line.southern_service_task_id, task)
        self.assertIn(labor.name, labor_line.name)
        self.assertIn(second_labor.name, labor_line.name)
        self.assertFalse(nonbillable.sale_line_id)
        self.assertEqual(part.sale_line_id.order_id, order)
        self.assertEqual(part.sale_line_id.southern_service_task_id, task)
        self.assertEqual(part.sale_line_id.product_id, self.part_product)
        self.assertEqual(part.sale_line_id.product_uom_qty, 2.0)
        self.assertEqual(part.sale_line_id.price_unit, 42.50)
        native_line = self.env["sale.order.line"].create(
            {
                "order_id": order.id,
                "product_id": self.part_product.id,
                "product_uom_qty": 1.0,
            }
        )
        self.assertEqual(native_line.southern_service_task_id, task)
        self.assertIn(native_line, task.southern_quote_line_ids)

        part_action = task.action_southern_add_part()
        self.assertEqual(
            part_action["res_model"],
            "southern.service.work.item",
        )
        self.assertEqual(part_action["context"]["default_work_type"], "part")
        self.assertEqual(part_action["context"]["default_task_id"], task.id)

        labor.allocated_hours = 4.25
        self.assertEqual(labor.quote_quantity, 4.25)
        self.assertEqual(labor.sale_line_id.product_uom_qty, 5.75)
        self.assertEqual(task.southern_service_task_hours, 6.25)
        self.assertEqual(task.allocated_hours, 6.25)
        self.assertEqual(order.southern_estimated_hours, 6.25)
        self.assertEqual(case.estimated_hours, 6.25)

    def test_service_job_uses_configured_default_labor_product(self):
        self.env["ir.config_parameter"].sudo().set_param(
            "southern_service_operations.default_labor_product_id",
            self.service_product.id,
        )
        case = self._create_customer_case()
        case.action_route_work()
        task = case.task_ids
        self.assertEqual(task.southern_labor_product_id, self.service_product)
        history_action = task.action_southern_open_equipment_history()
        self.assertEqual(history_action["res_model"], "equipment.details")
        self.assertEqual(history_action["res_id"], self.equipment.id)

    def test_internal_service_routes_to_maintenance(self):
        internal_equipment = self.env["maintenance.equipment"].create(
            {"name": "Southern Internal Test Equipment"}
        )
        case = self.env["southern.service.case"].create(
            {
                "service_domain": "internal",
                "maintenance_equipment_id": internal_equipment.id,
                "service_location": "internal",
                "commercial_basis": "internal",
                "complaint": "Internal development workflow test",
            }
        )

        case.action_route_work()
        self.assertEqual(len(case.maintenance_request_ids), 1)
        request = case.maintenance_request_ids
        self.assertEqual(request.equipment_id, internal_equipment)

        case.action_route_work()
        self.assertEqual(case.maintenance_request_ids, request)

    def test_digital_equipment_inspection_feeds_ai_evidence(self):
        case = self._create_customer_case()
        case.action_route_work()
        task = case.task_ids

        start_action = task.action_southern_start_inspection()
        self.assertEqual(task.southern_inspection_state, "in_progress")
        self.assertEqual(task.southern_inspector_id, self.env.user)
        self.assertTrue(task.southern_inspection_started_at)
        self.assertEqual(
            start_action,
            {"type": "ir.actions.client", "tag": "reload"},
        )
        finding_action = task.action_southern_add_inspection_finding()
        self.assertEqual(
            finding_action["res_model"],
            "southern.service.inspection.item",
        )
        self.assertEqual(
            finding_action["context"]["default_task_id"],
            task.id,
        )

        included = self.env["southern.service.inspection.item"].create(
            {
                "task_id": task.id,
                "inspection_area": "Hydraulics",
                "name": "Main hydraulic pump",
                "result": "repair",
                "priority": "high",
                "measurement": "1,850 psi",
                "fault_code": "HYD-LOW-P",
                "finding": "Pressure remained below specification during test.",
                "recommended_action": "Confirm relief setting and pump condition.",
            }
        )
        self.env["southern.service.inspection.item"].create(
            {
                "task_id": task.id,
                "inspection_area": "Safety",
                "name": "Internal-only observation",
                "result": "monitor",
                "include_in_ai": False,
            }
        )
        task.southern_inspection_summary = "Hydraulic pressure requires attention."

        service_input = task._southern_ai_input()
        inspection = service_input["digital_equipment_inspection"]
        self.assertEqual(inspection["status"], "in_progress")
        self.assertEqual(inspection["summary"], task.southern_inspection_summary)
        self.assertEqual(len(inspection["items"]), 1)
        self.assertEqual(inspection["items"][0]["area"], "Hydraulics")
        self.assertEqual(inspection["items"][0]["result"], "repair")
        self.assertEqual(inspection["items"][0]["fault_code"], "HYD-LOW-P")
        self.assertEqual(task.southern_inspection_attention_count, 2)

        complete_action = task.action_southern_complete_inspection()
        self.assertEqual(task.southern_inspection_state, "completed")
        self.assertTrue(task.southern_inspection_completed_at)
        self.assertEqual(
            complete_action,
            {"type": "ir.actions.client", "tag": "reload"},
        )
        self.assertIn(included, task.southern_inspection_item_ids)

        task.action_southern_reopen_inspection()
        self.assertEqual(task.southern_inspection_state, "in_progress")
        self.assertFalse(task.southern_inspection_completed_at)

    def test_service_photo_stays_with_job_equipment_and_ai_context(self):
        case = self._create_customer_case()
        case.action_route_work()
        task = case.task_ids
        action = task.action_southern_add_service_photo()
        self.assertEqual(action["res_model"], "southern.service.photo")
        self.assertEqual(action["context"]["default_task_id"], task.id)

        photo = self.env["southern.service.photo"].create(
            {
                "task_id": task.id,
                "image": (
                    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
                    b"AAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
                ),
                "category": "finding",
                "caption": "Hydraulic hose abrasion near boom pivot",
            }
        )
        self.assertEqual(photo.task_id, task)
        self.assertEqual(photo.client_equipment_id, self.equipment)
        self.assertEqual(task.southern_service_photo_count, 1)
        photo_context = task._southern_ai_input()[
            "digital_equipment_inspection"
        ]["photo_evidence"]
        self.assertEqual(len(photo_context), 1)
        self.assertIn("hose abrasion", photo_context[0]["caption"])

    def test_reviewed_ai_estimate_creates_native_tasks_parts_and_note(self):
        case = self._create_customer_case()
        case.action_route_work()
        task = case.task_ids
        task.write(
            {
                "southern_quote_workflow": True,
                "southern_labor_product_id": self.service_product.id,
            }
        )
        task.action_southern_create_quotation()
        suggestion = self.env["southern.service.ai.suggestion"].create(
            {
                "task_id": task.id,
                "model_name": "test-model",
                "summary": "Review hydraulic complaint",
                "customer_note": "Proposed inspection and repair estimate.",
                "confidence": "medium",
                "line_ids": [
                    Command.create(
                        {
                            "work_type": "labor",
                            "name": "Inspect hydraulic system",
                            "estimated_hours": 2.5,
                            "hours_low": 2.0,
                            "hours_high": 3.0,
                            "quantity": 0.0,
                            "confidence": "medium",
                        }
                    ),
                    Command.create(
                        {
                            "work_type": "part",
                            "name": "Hydraulic filter",
                            "quantity": 1.0,
                            "product_id": self.part_product.id,
                            "confidence": "medium",
                        }
                    ),
                ],
            }
        )

        self.assertIn("AI Estimate Review", suggestion.display_name)
        self.assertIn(task.name, suggestion.display_name)

        suggestion.action_apply_selected()

        self.assertEqual(suggestion.state, "applied")
        self.assertEqual(len(task.southern_service_work_item_ids), 2)
        self.assertTrue(task.southern_labor_sale_line_id)
        self.assertEqual(task.southern_labor_sale_line_id.product_uom_qty, 2.5)
        self.assertTrue(task.southern_ai_note_line_id)
        self.assertEqual(task.southern_ai_note_line_id.display_type, "line_note")
        self.assertIn("Proposed inspection", task.southern_ai_note_line_id.name)
        part_line = task.southern_quote_line_ids.filtered(
            lambda line: line.product_id == self.part_product
        )
        self.assertEqual(part_line.product_uom_qty, 1.0)
