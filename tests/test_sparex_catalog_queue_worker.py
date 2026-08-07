from __future__ import annotations

import unittest
from pathlib import Path

from scripts import sparex_catalog_queue_worker as worker


class SparexCatalogQueueWorkerTests(unittest.TestCase):
    def test_cli_converts_odoo_env_file_to_path(self):
        args = worker.build_parser().parse_args(
            [
                "--queue-url",
                "https://sqs.us-east-1.amazonaws.com/123/catalog.fifo",
                "--odoo-env-file",
                "/opt/southern-parts/Odoo/odoo_connection.env",
                "--wait-seconds",
                "0",
            ]
        )
        self.assertIsInstance(args.odoo_env_file, Path)
        self.assertEqual(args.wait_seconds, 0)


if __name__ == "__main__":
    unittest.main()
