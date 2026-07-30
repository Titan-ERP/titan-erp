from odoo import fields, models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    southern_demand_type = fields.Selection(
        [
            ("stock", "Stock"),
            ("sale", "Sales"),
            ("service", "Service"),
            ("internal", "Internal Service"),
        ],
        string="Demand Type",
        default="stock",
        index=True,
    )
    southern_service_case_id = fields.Many2one(
        "southern.service.case",
        string="Service Case",
        index=True,
        ondelete="set null",
    )
    southern_task_id = fields.Many2one(
        "project.task",
        string="Scheduled Work",
        ondelete="set null",
    )
    southern_repair_order_id = fields.Many2one(
        "repair.order",
        string="Shop Work",
        ondelete="set null",
    )
    southern_maintenance_request_id = fields.Many2one(
        "maintenance.request",
        string="Internal Work",
        ondelete="set null",
    )


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    southern_service_case_count = fields.Integer(
        compute="_compute_southern_service_case_count"
    )

    def _compute_southern_service_case_count(self):
        for order in self:
            order.southern_service_case_count = len(
                order.order_line.southern_service_case_id
            )

    def action_view_southern_service_cases(self):
        self.ensure_one()
        case_ids = self.order_line.southern_service_case_id.ids
        return {
            "type": "ir.actions.act_window",
            "name": "Service Cases",
            "res_model": "southern.service.case",
            "view_mode": "list,form",
            "domain": [("id", "in", case_ids)],
        }
