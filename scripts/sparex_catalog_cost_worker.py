"""Recover one bounded batch of exact dealer costs into durable Sparex staging."""

from __future__ import annotations

import argparse
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path

from scripts.odoo_runtime import ArtifactStore, OdooClient, OdooConfig
from scripts.sparex_catalog_agents.cost_recovery import PortalCooldownError, recover_dealer_costs

CONFIRMATION = "sparex-durable-cost-recovery"
PORTAL_COOLDOWN_EXIT_CODE = 75


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--odoo-env-file", type=Path, required=True)
    parser.add_argument("--dealer-env-file", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--s3-bucket", required=True)
    parser.add_argument("--s3-prefix", default="sparex-product-catalog/dealer-cost/production")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--throttle-seconds", type=float, default=3.0)
    parser.add_argument("--worker-id", default=socket.gethostname())
    parser.add_argument("--confirm", required=True)
    parser.add_argument("--reason", required=True)
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        raise RuntimeError("Exact dealer-cost recovery requires explicit confirmation.")
    if os.environ.get("ODOO_WRITE_ENABLED", "").strip().casefold() not in {"1", "true", "yes"}:
        raise RuntimeError("ODOO_WRITE_ENABLED must be true for dealer-cost staging.")

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    store = ArtifactStore(args.artifact_root / stamp, schema_version="1.1")
    client = OdooClient(OdooConfig.from_env(args.odoo_env_file)).connect()
    os.environ["SPAREX_DURABLE_CATALOG_PIPELINE"] = "true"
    try:
        result = recover_dealer_costs(
            client,
            worker_id=args.worker_id,
            limit=max(1, min(args.limit, 5)),
            dealer_env_file=args.dealer_env_file,
            throttle_seconds=max(3.0, args.throttle_seconds),
            store=store,
            artifact_root=args.artifact_root,
            s3_bucket=args.s3_bucket,
            s3_prefix=f"{args.s3_prefix.rstrip('/')}/{stamp}",
            reason=args.reason,
        )
    except PortalCooldownError:
        print(json.dumps({"state": "portal_cooldown", "write_blocked": True}, sort_keys=True))
        return PORTAL_COOLDOWN_EXIT_CODE
    print(json.dumps(result, sort_keys=True))
    if result.get("state") == "portal_cooldown":
        return PORTAL_COOLDOWN_EXIT_CODE
    return 2 if result.get("write_blocked") else 0


if __name__ == "__main__":
    raise SystemExit(main())
