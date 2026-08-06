"""Generate the blocking Sparex uniqueness report before catalog indexes are enabled."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from scripts.odoo_runtime import OdooClient, OdooConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    report = client.call(
        "southern.sparex.catalog.ingestion",
        "conflict_preflight",
        limit=max(1, min(args.limit, 1_000)),
    )
    content = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8") + b"\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(content)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": hashlib.sha256(content).hexdigest(),
                "blocking": bool(report.get("blocking")),
                "reported_conflict_groups": int(report.get("reported_conflict_groups") or 0),
            },
            sort_keys=True,
        )
    )
    return 2 if report.get("blocking") else 0


if __name__ == "__main__":
    raise SystemExit(main())
