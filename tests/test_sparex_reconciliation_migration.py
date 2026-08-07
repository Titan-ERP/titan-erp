import importlib.util
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "southern_parts_intelligence"
    / "migrations"
    / "19.0.1.46.0"
    / "post-migrate.py"
)


class Cursor:
    def __init__(self):
        self.query = ""

    def execute(self, query):
        self.query = query


def test_migration_restores_verified_pending_items_without_touching_stale_items():
    spec = importlib.util.spec_from_file_location("sparex_reconciliation_migration", MIGRATION_PATH)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    cursor = Cursor()

    migration.migrate(cursor, "19.0.1.46.0")

    assert "SET reconciliation_state = 'current'" in cursor.query
    assert "WHERE reconciliation_state = 'pending'" in cursor.query
    assert "state = 'verified'" in cursor.query
    assert "source_state = 'verified'" in cursor.query
    assert "odoo_match_state IN ('matched_active', 'missing')" in cursor.query
