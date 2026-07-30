from odoo import Command
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
                "complaint": "Development workflow test",
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

        case.action_route_work()
        self.assertEqual(case.task_ids, task)

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
