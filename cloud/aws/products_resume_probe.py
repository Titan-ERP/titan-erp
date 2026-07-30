from __future__ import annotations

import json
import os
import sys
from pathlib import Path


ROOT = Path(os.environ.get("SOUTHERN_WORKER_ROOT", "/opt/southern-parts/Odoo"))
sys.path.insert(0, str(ROOT / "scripts"))

import sparex_dealer_portal_sync as dealer_sync  # noqa: E402


def main() -> int:
    os.chdir(ROOT)
    dealer_sync.load_env()
    conn = dealer_sync.connect_odoo()
    products = dealer_sync.execute(
        conn,
        "product.template",
        "search_read",
        [[
            ("id", ">", 12009),
            ("default_code", "=like", "S.%"),
            ("standard_price", "<=", 0),
        ]],
        {
            "fields": [
                "id",
                "default_code",
                "name",
                "standard_price",
                "southern_source_url",
                "active",
                "sale_ok",
            ],
            "limit": 5,
            "order": "id",
            "context": {"active_test": False},
        },
    )
    suppliers = dealer_sync.execute(
        conn,
        "res.partner",
        "search_read",
        [[("name", "=", "Sparex")]],
        {"fields": ["id", "name"], "limit": 2},
    )
    print(
        json.dumps(
            {
                "resume_after_product_id": 12009,
                "candidate_count": len(products),
                "candidates": products,
                "sparex_supplier_matches": len(suppliers),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
