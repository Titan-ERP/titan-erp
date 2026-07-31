from __future__ import annotations

import unittest
from pathlib import Path

from scripts.audit_apply_workflows import inventory

ROOT = Path(__file__).resolve().parents[1]


class ApplyWorkflowControlTests(unittest.TestCase):
    def test_every_apply_capable_workflow_is_explicit_opt_in(self):
        workflows = inventory()
        unsafe = [row["workflow"] for row in workflows if not row["explicit_apply_opt_in"]]
        self.assertGreaterEqual(len(workflows), 80)
        self.assertEqual(unsafe, [])

    def test_migrated_high_risk_workflows_use_apply_gate(self):
        workflows = [
            "odoo_apply_partner_price_proposals.py",
            "odoo_apply_retail_price_proposals.py",
            "odoo_apply_sparex_supplier_costs.py",
            "odoo_apply_sparex_website_prices.py",
        ]
        for workflow in workflows:
            with self.subTest(workflow=workflow):
                source = (ROOT / "scripts" / workflow).read_text(encoding="utf-8")
                self.assertIn("ApplyGate(", source)
                self.assertIn("--confirm", source)
                self.assertIn("--reason", source)
                self.assertIn("--max-records", source)


if __name__ == "__main__":
    unittest.main()
