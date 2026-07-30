from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.odoo_runtime import (
    ApplyGate,
    ArtifactStore,
    ContactCandidate,
    ContactIdentity,
    WriteBlocked,
    choose_contact_match,
    classify_crm_rows,
)


class ContactMatchingTests(unittest.TestCase):
    def test_exact_email_wins(self):
        decision = choose_contact_match(
            ContactIdentity("Acme Equipment", "SALES@ACME.COM", ""),
            [
                ContactCandidate(1, "Acme", "sales@acme.com"),
                ContactCandidate(2, "Acme Equipment"),
            ],
        )
        self.assertEqual(decision.status, "matched")
        self.assertEqual(decision.partner_id, 1)

    def test_ambiguous_name_requires_review(self):
        decision = choose_contact_match(
            ContactIdentity("Acme LLC"),
            [ContactCandidate(1, "Acme LLC"), ContactCandidate(2, "ACME, LLC")],
        )
        self.assertEqual(decision.status, "review")
        self.assertIsNone(decision.partner_id)


class ApplyGateTests(unittest.TestCase):
    def test_write_requires_environment_confirmation_reason_and_limit(self):
        gate = ApplyGate("pricing", True, "pricing", "approved review", 2)
        with patch.dict(os.environ, {"ODOO_WRITE_ENABLED": "true"}, clear=False):
            gate.authorize(2)
            with self.assertRaises(WriteBlocked):
                gate.authorize(3)

    def test_write_disabled_by_default(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(WriteBlocked):
                ApplyGate("pricing", True, "pricing", "approved", 10).authorize(1)


class ArtifactTests(unittest.TestCase):
    def test_artifact_is_versioned_and_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest = ArtifactStore(root, minimum_free_bytes=0).write_json("sample.json", {"ok": True})
            self.assertEqual(len(manifest["sha256"]), 64)
            stored = json.loads((root / "sample.json").read_text(encoding="utf-8"))
            self.assertEqual(stored["schema_version"], "1.0")
            self.assertTrue((root / "manifest.jsonl").exists())


class CrmClassificationTests(unittest.TestCase):
    def test_mass_unworked_cohort_is_reference_data(self):
        rows = [
            {
                "id": index,
                "create_date": "2026-07-10 00:53:09",
                "stage_id": [1, "New"],
                "user_id": [2, "Administrator"],
                "probability": 50,
                "expected_revenue": 0,
            }
            for index in range(60)
        ]
        classified = classify_crm_rows(rows)
        self.assertEqual({row["record_class"] for row in classified}, {"imported_reference"})

    def test_commercial_signal_makes_actual_opportunity(self):
        rows = [
            {
                "id": index,
                "create_date": "2026-07-10 00:53:09",
                "stage_id": [1, "New"],
                "user_id": [2, "Administrator"],
                "probability": 50,
                "expected_revenue": 0,
            }
            for index in range(60)
        ]
        rows[0]["expected_revenue"] = 5000
        classified = classify_crm_rows(rows)
        self.assertEqual(classified[0]["record_class"], "actual_opportunity")


if __name__ == "__main__":
    unittest.main()
