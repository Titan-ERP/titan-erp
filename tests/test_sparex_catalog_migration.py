import importlib.util
import unittest
from pathlib import Path

MIGRATION_PATH = (
    Path(__file__).resolve().parents[1]
    / "southern_parts_intelligence"
    / "migrations"
    / "19.0.1.45.0"
    / "post-migrate.py"
)


class Cursor:
    def __init__(self):
        self.query = ""

    def execute(self, query):
        self.query = query

    def fetchall(self):
        return []


class SparexCatalogMigrationTests(unittest.TestCase):
    def test_migration_reads_stored_publication_column(self):
        spec = importlib.util.spec_from_file_location("sparex_catalog_migration", MIGRATION_PATH)
        migration = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(migration)
        cursor = Cursor()

        migration.migrate(cursor, "19.0.1.45.0")

        self.assertIn("product.is_published", cursor.query)
        self.assertNotIn("product.website_published", cursor.query)
