"""Repair the exact internal-placeholder copy on otherwise-ready Sparex products.

Dry-run is the default. Apply mode records a complete rollback snapshot and
requires the shared supervised ApplyGate confirmation.
"""

from __future__ import annotations

import argparse
import html
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

if __package__:
    from scripts.odoo_runtime import ApplyGate, OdooClient, OdooConfig
    from scripts.odoo_runtime.safety import append_audit
    from scripts.odoo_sparex_publication_safeguard import collect_target, connect
else:
    from odoo_runtime import ApplyGate, OdooClient, OdooConfig
    from odoo_runtime.safety import append_audit
    from odoo_sparex_publication_safeguard import collect_target, connect


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "outputs" / "sparex_description_repair"
WORKFLOW = "sparex-placeholder-description-repair"
EXACT_BLOCKERS = ["placeholder_customer_description"]
DESCRIPTION_FIELDS = ("description_ecommerce", "website_description", "description_sale")


def customer_copy(name: str, sku: str) -> dict[str, str]:
    safe_name = html.escape(" ".join((name or "").split()))
    safe_sku = html.escape(" ".join((sku or "").split()))
    ecommerce = (
        f"<p>{safe_name} is available from Southern Equipment under Sparex reference {safe_sku}.</p>"
        "<p>Southern Equipment confirms availability, fitment, and pickup or shipping details before fulfillment.</p>"
    )
    sale = (
        f"{html.unescape(safe_name)}. Sparex reference {html.unescape(safe_sku)}. "
        "Confirm availability and fitment with Southern Equipment."
    )
    return {
        "description_ecommerce": ecommerce,
        "website_description": ecommerce,
        "description_sale": sale,
    }


def _client(env_file: Path) -> OdooClient:
    return OdooClient(OdooConfig.from_env(env_file.resolve())).connect()


def collect_candidates(env_file: Path) -> tuple[OdooClient, list[dict[str, Any]]]:
    db, uid, key, models = connect(env_file.resolve())
    target, _fields = collect_target(models, db, uid, key, scope="strict")
    ids = [row["product_id"] for row in target if row["blockers"] == EXACT_BLOCKERS]
    client = _client(env_file)
    products = client.call(
        "product.template",
        "read",
        ids=ids,
        fields=["id", "default_code", "name", "website_published", *DESCRIPTION_FIELDS, "write_date"],
        context={"active_test": False},
    )
    by_id = {int(row["product_id"]): row for row in target}
    records = []
    for product in products:
        product_id = int(product["id"])
        records.append(
            {
                "product_id": product_id,
                "sku": str(product.get("default_code") or ""),
                "name": str(product.get("name") or ""),
                "write_date_before": str(product.get("write_date") or ""),
                "descriptions_before": {field: str(product.get(field) or "") for field in DESCRIPTION_FIELDS},
                "descriptions_after": customer_copy(
                    str(product.get("name") or ""), str(product.get("default_code") or "")
                ),
                "blockers_before": by_id[product_id]["blockers"],
            }
        )
    return client, records


def write_snapshot(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": "1.0",
                "workflow": WORKFLOW,
                "created_at_utc": datetime.now(UTC).isoformat(),
                "records": records,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )


def restore(args: argparse.Namespace) -> int:
    payload = json.loads(args.restore_from.read_text(encoding="utf-8"))
    if payload.get("workflow") != WORKFLOW or payload.get("schema_version") != "1.0":
        raise RuntimeError("Restore input is not a compatible description-repair snapshot.")
    records = payload.get("records") or []
    gate = ApplyGate(WORKFLOW, args.apply, args.confirm, args.reason, args.max_records)
    if args.apply:
        gate.authorize(len(records))
        client = _client(args.env_file)
        append_audit(REPORT_DIR / "write_audit.jsonl", gate.audit_row({"restore": True}, len(records)))
        for record in records:
            client.call(
                "product.template",
                "write",
                ids=[int(record["product_id"])],
                vals=record["descriptions_before"],
            )
    print(json.dumps({"mode": "restore_apply" if args.apply else "restore_dry_run", "records": len(records)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--restore-from", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=1000)
    args = parser.parse_args()
    if args.restore_from:
        return restore(args)

    client, records = collect_candidates(args.env_file)
    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    snapshot = REPORT_DIR / f"sparex_description_repair_{stamp}.json"
    write_snapshot(snapshot, records)
    if args.apply:
        gate = ApplyGate(WORKFLOW, True, args.confirm, args.reason, args.max_records)
        gate.authorize(len(records))
        append_audit(REPORT_DIR / "write_audit.jsonl", gate.audit_row({"snapshot": str(snapshot)}, len(records)))
        for record in records:
            client.call(
                "product.template",
                "write",
                ids=[int(record["product_id"])],
                vals=record["descriptions_after"],
            )

    _client_after, remaining = collect_candidates(args.env_file)
    summary = {
        "mode": "apply" if args.apply else "dry_run",
        "matched": len(records),
        "changed": len(records) if args.apply else 0,
        "remaining": len(remaining),
        "snapshot": str(snapshot),
    }
    print(json.dumps(summary, sort_keys=True))
    if args.apply and remaining:
        raise RuntimeError(f"Verification failed: {len(remaining)} placeholder descriptions remain.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
