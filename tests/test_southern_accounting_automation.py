import ast
import csv
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "southern_accounting_guardrails"


class SouthernAccountingAutomationStaticTests(unittest.TestCase):
    def test_manifest_includes_accounting_automation_views(self):
        manifest = ast.literal_eval((MODULE / "__manifest__.py").read_text(encoding="utf-8"))
        self.assertIn("views/accounting_automation_views.xml", manifest["data"])
        self.assertIn("views/stripe_payout_views.xml", manifest["data"])

    def test_new_models_have_access_rules(self):
        with (MODULE / "security" / "ir.model.access.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
            model_ids = {row["model_id:id"] for row in rows}
        self.assertIn("model_southern_accounting_automation_policy", model_ids)
        self.assertIn("model_southern_accounting_automation_run", model_ids)
        self.assertIn("model_southern_accounting_automation_finding", model_ids)
        self.assertIn("model_southern_stripe_payout_evidence", model_ids)
        worker_move_line = [
            row
            for row in rows
            if row["model_id:id"] == "account.model_account_move_line"
            and "automation_worker" in row["group_id:id"]
        ]
        self.assertTrue(worker_move_line)
        self.assertTrue(all(row["perm_write"] == "0" for row in worker_move_line))

    def test_views_parse(self):
        ET.parse(MODULE / "views" / "accounting_automation_views.xml")
        ET.parse(MODULE / "views" / "stripe_payout_views.xml")
        ET.parse(MODULE / "views" / "bank_coding_views.xml")
        ET.parse(MODULE / "views" / "southern_accounting_menus.xml")

    def test_candidate_separates_ai_confidence_from_authority(self):
        source = (MODULE / "models" / "bank_coding.py").read_text(encoding="utf-8")
        for token in (
            "match_type",
            "deterministic_match",
            "ai_confidence",
            "policy_eligible",
            "auto_apply_eligible",
        ):
            self.assertIn(token, source)
        self.assertIn('candidate.match_type != "deterministic"', source)
        self.assertIn("not candidate.deterministic_match", source)

    def test_candidate_fingerprint_and_reason_codes_exist(self):
        source = (MODULE / "models" / "bank_coding.py").read_text(encoding="utf-8")
        self.assertIn("build_evaluation_hash", source)
        self.assertIn("build_application_key", source)
        self.assertIn("hashlib.sha256", source)
        self.assertIn("SNAPSHOT_CHANGED", source)
        for fingerprint_part in (
            "target_account.id",
            "bank_line.is_reconciled",
            "rule.id",
            "policy.id",
        ):
            self.assertIn(fingerprint_part, source)
        for reason in (
            "OVER_LINE_LIMIT",
            "DAILY_LIMIT_REACHED",
            "MERCHANT_SETTLEMENT",
            "GENERIC_CHECK",
            "TRANSFER_RISK",
            "LOAN_PAYMENT",
            "TAX_PAYMENT",
            "MULTIPLE_SUSPENSE_LINES",
            "ALREADY_RECONCILED",
            "PROTECTED_ACCOUNT",
            "NO_DETERMINISTIC_RULE",
            "COMPANY_MISMATCH",
        ):
            self.assertIn(reason, source)

    def test_guarded_apply_is_separate_from_manual_apply(self):
        source = (MODULE / "models" / "bank_coding.py").read_text(encoding="utf-8")
        self.assertIn("def guarded_apply_candidate", source)
        self.assertIn("candidate.guarded_apply_candidate()", source)
        self.assertIn("def action_apply", source)
        self.assertIn("_prepare_guarded_apply", source)
        self.assertIn("_assert_company_isolation", source)

    def test_candidate_lifecycle_and_idempotency_fields_exist(self):
        source = (MODULE / "models" / "bank_coding.py").read_text(encoding="utf-8")
        for token in (
            "application_key",
            "application_attempted_at",
            "application_result",
            "application_error",
            "unique(application_key)",
        ):
            self.assertIn(token, source)
        for state in (
            "observed",
            "evaluated",
            "candidate",
            "eligible",
            "review_required",
            "blocked",
            "stale",
            "applied",
        ):
            self.assertIn(f'("{state}",', source)

    def test_policy_has_versioning_and_rollout_modes(self):
        source = (MODULE / "models" / "accounting_automation.py").read_text(encoding="utf-8")
        self.assertIn("policy_version", source)
        self.assertIn("effective_from", source)
        self.assertIn("superseded_by_id", source)
        self.assertIn("guarded_apply", source)
        self.assertIn("emergency_stop", source)
        self.assertIn("candidate authorization", source)

    def test_scheduled_bank_coding_defaults_to_observe_without_policy(self):
        source = (MODULE / "models" / "bank_coding.py").read_text(encoding="utf-8")
        self.assertIn('"mode": policy.mode if policy else "observe"', source)
        self.assertNotIn('or "candidate"', source)

    def test_aws_worker_uses_odoo_bank_coding_gate(self):
        source = (ROOT / "scripts" / "odoo_accounting_automation_worker.py").read_text(encoding="utf-8")
        self.assertIn('"southern.bank.coding.run"', source)
        self.assertIn('"action_evaluate"', source)
        self.assertNotIn('"account.move.line",\\n        "write"', source)

    def test_stripe_payout_evidence_model_tracks_bank_linkage(self):
        source = (MODULE / "models" / "stripe_payout.py").read_text(encoding="utf-8")
        for token in (
            "_name = \"southern.stripe.payout.evidence\"",
            "stripe_payout_id",
            "gross_charges",
            "stripe_fees",
            "processing_fee_charged",
            "processing_fee_margin",
            "expected_net",
            "stripe_payout_net",
            "matched_bank_line_ids",
            "stripe_clearing_move_ids",
            "stripe_bridge_move_ids",
            "matched_payment_ids",
            "linked_invoice_ids",
            "unique(company_id, stripe_payout_id)",
            "upsert_from_worker",
        ):
            self.assertIn(token, source)

    def test_stripe_payout_observe_can_write_evidence_without_accounting_mutation(self):
        source = (ROOT / "scripts" / "stripe_payout_observe.py").read_text(encoding="utf-8")
        for token in (
            "--write-odoo-evidence",
            "write_odoo_payout_evidence",
            "southern.stripe.payout.evidence",
            "upsert_from_worker",
            "bridge_bank_line_ids",
            "matched_bank_line_ids",
        ):
            self.assertIn(token, source)
        self.assertNotIn('"account.move.line", "write"', source)
        self.assertNotIn('"account.move", "action_post"', source)


if __name__ == "__main__":
    unittest.main()
