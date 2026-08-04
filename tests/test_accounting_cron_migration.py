import importlib.util
import unittest
from pathlib import Path

MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "southern_accounting_guardrails"
    / "migrations"
    / "19.0.1.5.2"
    / "post-migrate.py"
)
SPEC = importlib.util.spec_from_file_location("accounting_cron_migration", MODULE_PATH)
accounting_cron_migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(accounting_cron_migration)


class RecordingCursor:
    def __init__(self):
        self.calls = []

    def execute(self, query, params):
        self.calls.append((query, params))


class AccountingCronMigrationTest(unittest.TestCase):
    def test_migration_disables_crons_and_clears_noupdate(self):
        cursor = RecordingCursor()

        accounting_cron_migration.migrate(cursor, "19.0.1.5.1")

        self.assertEqual(len(cursor.calls), 2)
        self.assertIn("SET active = FALSE", cursor.calls[0][0])
        self.assertIn("SET noupdate = FALSE", cursor.calls[1][0])
        for _query, params in cursor.calls:
            self.assertEqual(params[0], "southern_accounting_guardrails")
            self.assertEqual(
                params[1],
                accounting_cron_migration.CRON_XMLIDS,
            )


if __name__ == "__main__":
    unittest.main()
