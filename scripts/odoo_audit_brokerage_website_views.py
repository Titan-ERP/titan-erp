"""Find and optionally repair contradictory brokerage verification copy."""

from __future__ import annotations

import argparse
import os
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--search-text", default="Verified listing")
    parser.add_argument("--fix-listing-count", action="store_true")
    args = parser.parse_args()
    search_text = "Showing" if args.fix_listing_count else args.search_text
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(
        db, username, api_key, {}
    )
    if not uid:
        raise RuntimeError("Odoo authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    rows = models.execute_kw(
        db,
        uid,
        api_key,
        "ir.ui.view",
        "search_read",
        [[("arch_db", "ilike", search_text)]],
        {
            "fields": ["id", "name", "key", "website_id", "active", "arch_db"],
            "limit": 20,
        },
    )
    if args.fix_listing_count:
        rows = [
            row
            for row in rows
            if row.get("key")
            == "southern_equipment_brokerage.website_equipment_listings"
        ]
    print(f"MATCHES={len(rows)}")
    if args.apply and args.search_text != "Verified listing" and not args.fix_listing_count:
        raise RuntimeError("--apply currently supports only the exact verified-badge repair.")
    if args.apply and len(rows) != 1:
        raise RuntimeError(
            f"Expected exactly one contradictory live view; found {len(rows)}."
        )
    for row in rows:
        arch = str(row.get("arch_db") or "")
        marker = arch.lower().find(search_text.lower())
        start = max(0, marker - 500)
        end = min(len(arch), marker + 500)
        print(f"VIEW={row['id']}|{row.get('key')}|{row.get('name')}")
        print(arch[start:end].replace("\n", " "))
        if args.apply:
            if args.fix_listing_count:
                old = "pager.get('total', 0)"
                new = "len(listings)"
                if arch.count(old) != 2:
                    raise RuntimeError(
                        "The expected listing-count expression was not found exactly twice."
                    )
            else:
                old = '<span t-else="" class="badge rounded-pill text-bg-dark">Verified listing</span>'
                new = '<span t-else="" class="badge rounded-pill text-bg-dark">Broker-assisted sourced listing</span>'
                if arch.count(old) != 1:
                    raise RuntimeError("The expected badge markup was not found exactly once.")
            models.execute_kw(
                db,
                uid,
                api_key,
                "ir.ui.view",
                "write",
                [[row["id"]], {"arch_db": arch.replace(old, new)}],
            )
            print(f"UPDATED_VIEW={row['id']}")
    if args.apply:
        if args.fix_listing_count:
            refreshed = models.execute_kw(
                db,
                uid,
                api_key,
                "ir.ui.view",
                "read",
                [[rows[0]["id"]]],
                {"fields": ["arch_db"]},
            )[0]["arch_db"]
            remaining = str(refreshed).count("pager.get('total', 0)")
            print(f"REMAINING_BROKEN_COUNT_EXPRESSIONS={remaining}")
        else:
            remaining = models.execute_kw(
                db,
                uid,
                api_key,
                "ir.ui.view",
                "search_count",
                [[("arch_db", "ilike", "Verified listing")]],
            )
            print(f"REMAINING_CONTRADICTORY_VIEWS={remaining}")
        if remaining:
            raise RuntimeError("Requested website-view repair did not fully reconcile.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
