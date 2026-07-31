"""Reversibly unpublish Sparex products that fail the sourcing publication gate.

Dry-run is the default. Apply mode requires the shared supervised ApplyGate.
The generated snapshot is also the only accepted input for restore mode.
"""

from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from scripts.odoo_runtime import ApplyGate, OdooClient, OdooConfig
    from scripts.odoo_runtime.safety import append_audit
else:
    from odoo_runtime import ApplyGate, OdooClient, OdooConfig
    from odoo_runtime.safety import append_audit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ENV = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "outputs" / "sparex_publication_safeguard"
WORKFLOW = "sparex-publication-safeguard"


def money(value: Any) -> float:
    try:
        return round(float(value or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def is_sparex_reference(value: Any) -> bool:
    return str(value or "").strip().upper().startswith("S.")


def publication_blockers(product: dict[str, Any], supplier_prices: list[float]) -> list[str]:
    """Return strict, deterministic blockers for one published Sparex product."""
    blockers: list[str] = []
    positive_costs = [money(value) for value in supplier_prices if money(value) > 0]
    verified_cost = min(positive_costs) if positive_costs else 0.0
    sale_price = money(product.get("list_price"))
    source_url = str(product.get("southern_source_url") or "").strip()
    descriptions = [
        str(product.get(field) or "").strip()
        for field in ("description_ecommerce", "website_description", "description_sale")
        if field in product
    ]

    if verified_cost <= 0:
        blockers.append("missing_positive_supplier_cost")
    if not source_url.startswith("https://us.sparex.com/"):
        blockers.append("missing_verified_sparex_source")
    if sale_price <= 1.49:
        blockers.append("placeholder_sales_price")
    if verified_cost > 0 and sale_price <= verified_cost:
        blockers.append("sales_price_not_above_supplier_cost")
    if not product.get("public_categ_ids"):
        blockers.append("missing_website_category")
    if not product.get("image_1920"):
        blockers.append("missing_image")
    if descriptions and not any(descriptions):
        blockers.append("missing_customer_description")
    return blockers


def chunks(values: list[int], size: int = 400):
    for index in range(0, len(values), size):
        yield values[index : index + size]


def execute(models, db: str, uid: int, key: str, model: str, method: str, args=None, kwargs=None):
    return models.execute_kw(db, uid, key, model, method, args or [], kwargs or {})


class _ModelsAdapter:
    """Keep the script call sites API-mode neutral."""

    def __init__(self, client: OdooClient):
        self.client = client

    def execute_kw(self, _db, _uid, _key, model, method, args, kwargs):
        return self.client.execute(model, method, args, kwargs)


def connect(env_file: Path):
    config = OdooConfig.from_env(env_file)
    client = OdooClient(config).connect()
    return config.database, client.uid or 0, config.api_key, _ModelsAdapter(client)


def published_fields(product_fields: dict[str, Any]) -> list[str]:
    fields = [
        name
        for name in ("is_published", "website_published")
        if name in product_fields and not product_fields[name].get("readonly")
    ]
    if not fields:
        raise RuntimeError("No writable product publication field is available.")
    return fields


def collect_target(models, db: str, uid: int, key: str, *, scope: str) -> tuple[list[dict[str, Any]], list[str]]:
    fields_get = execute(models, db, uid, key, "product.template", "fields_get", [], {"attributes": ["readonly"]})
    publish_fields = published_fields(fields_get)
    primary_publish = "website_published" if "website_published" in fields_get else "is_published"
    domain = [
        ("active", "=", True),
        (primary_publish, "=", True),
        ("default_code", "=ilike", "S.%"),
    ]
    wanted = [
        "id",
        "default_code",
        "name",
        "list_price",
        "standard_price",
        "public_categ_ids",
        "seller_ids",
        *publish_fields,
    ]
    for optional in (
        "southern_source_url",
        "description_ecommerce",
        "website_description",
        "description_sale",
        "image_1920",
    ):
        if optional in fields_get:
            wanted.append(optional)

    product_ids = execute(
        models,
        db,
        uid,
        key,
        "product.template",
        "search",
        [domain],
        {"context": {"active_test": False}, "order": "id"},
    )
    products: list[dict[str, Any]] = []
    for id_chunk in chunks(product_ids):
        products.extend(
            execute(
                models,
                db,
                uid,
                key,
                "product.template",
                "read",
                [id_chunk],
                {"fields": wanted, "context": {"active_test": False, "bin_size": True}},
            )
        )

    suppliers = execute(
        models,
        db,
        uid,
        key,
        "res.partner",
        "search_read",
        [[("name", "=ilike", "Sparex")]],
        {"fields": ["id", "name"], "limit": 2},
    )
    if len(suppliers) != 1:
        raise RuntimeError("Exactly one existing Sparex supplier is required for publication verification.")
    sparex_supplier_id = int(suppliers[0]["id"])

    prices_by_product: dict[int, list[float]] = defaultdict(list)
    for id_chunk in chunks(product_ids):
        supplier_rows = execute(
            models,
            db,
            uid,
            key,
            "product.supplierinfo",
            "search_read",
            [[("product_tmpl_id", "in", id_chunk), ("partner_id", "=", sparex_supplier_id)]],
            {"fields": ["product_tmpl_id", "price"]},
        )
        for supplier in supplier_rows:
            product = supplier.get("product_tmpl_id")
            product_id = int(product[0]) if isinstance(product, list) and product else 0
            if product_id:
                prices_by_product[product_id].append(money(supplier.get("price")))

    target: list[dict[str, Any]] = []
    for product in products:
        if not is_sparex_reference(product.get("default_code")):
            continue
        blockers = publication_blockers(product, prices_by_product.get(int(product["id"]), []))
        if scope == "placeholder":
            blockers = [item for item in blockers if item == "placeholder_sales_price"]
        if not blockers:
            continue
        positive = [value for value in prices_by_product.get(int(product["id"]), []) if value > 0]
        target.append(
            {
                "product_id": int(product["id"]),
                "internal_reference": str(product.get("default_code") or ""),
                "name": str(product.get("name") or ""),
                "list_price": money(product.get("list_price")),
                "verified_supplier_cost": min(positive) if positive else 0.0,
                "source_url": str(product.get("southern_source_url") or ""),
                "publication_fields_before": {field: bool(product.get(field)) for field in publish_fields},
                "blockers": blockers,
            }
        )
    return target, publish_fields


def write_snapshot(path: Path, target: list[dict[str, Any]], *, scope: str) -> None:
    payload = {
        "schema_version": "1.0",
        "workflow": WORKFLOW,
        "scope": scope,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "records": target,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def write_csv_report(path: Path, target: list[dict[str, Any]], action: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "Product ID",
                "Internal Reference",
                "Name",
                "Sales Price",
                "Verified Supplier Cost",
                "Blockers",
                "Action",
            ],
        )
        writer.writeheader()
        for row in target:
            writer.writerow(
                {
                    "Product ID": row["product_id"],
                    "Internal Reference": row["internal_reference"],
                    "Name": row["name"],
                    "Sales Price": row["list_price"],
                    "Verified Supplier Cost": row["verified_supplier_cost"],
                    "Blockers": "; ".join(row["blockers"]),
                    "Action": action,
                }
            )


def restore_from_snapshot(args: argparse.Namespace, models, db: str, uid: int, key: str) -> int:
    payload = json.loads(args.restore_from.read_text(encoding="utf-8"))
    if payload.get("workflow") != WORKFLOW or payload.get("schema_version") != "1.0":
        raise RuntimeError("Restore input is not a compatible safeguard snapshot.")
    records = payload.get("records") or []
    ids = [int(row["product_id"]) for row in records]
    gate = ApplyGate(WORKFLOW, args.apply, args.confirm, args.reason, args.max_records)
    if args.apply:
        gate.authorize(len(ids))
        append_audit(REPORT_DIR / "write_audit.jsonl", gate.audit_row({"restore": ids}, len(ids)))
        fields_by_value: dict[tuple[tuple[str, bool], ...], list[int]] = defaultdict(list)
        for row in records:
            values = tuple(sorted((row.get("publication_fields_before") or {}).items()))
            fields_by_value[values].append(int(row["product_id"]))
        for value_items, product_ids in fields_by_value.items():
            for id_chunk in chunks(product_ids):
                execute(models, db, uid, key, "product.template", "write", [id_chunk, dict(value_items)])
    print(json.dumps({"mode": "restore_apply" if args.apply else "restore_dry_run", "records": len(ids)}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV)
    parser.add_argument("--scope", choices=("strict", "placeholder"), default="strict")
    parser.add_argument("--restore-from", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--reason", default="")
    parser.add_argument("--max-records", type=int, default=4000)
    args = parser.parse_args()

    db, uid, key, models = connect(args.env_file.resolve())
    if args.restore_from:
        return restore_from_snapshot(args, models, db, uid, key)

    target, publish_fields = collect_target(models, db, uid, key, scope=args.scope)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    snapshot = REPORT_DIR / f"sparex_publication_snapshot_{args.scope}_{stamp}.json"
    report = REPORT_DIR / f"sparex_publication_safeguard_{args.scope}_{stamp}.csv"
    write_snapshot(snapshot, target, scope=args.scope)
    write_csv_report(report, target, "Unpublished" if args.apply else "Would unpublish")

    ids = [int(row["product_id"]) for row in target]
    if args.apply:
        gate = ApplyGate(WORKFLOW, True, args.confirm, args.reason, args.max_records)
        gate.authorize(len(ids))
        append_audit(REPORT_DIR / "write_audit.jsonl", gate.audit_row({"scope": args.scope, "ids": ids}, len(ids)))
        values = {field: False for field in publish_fields}
        for id_chunk in chunks(ids):
            execute(models, db, uid, key, "product.template", "write", [id_chunk, values])

    remaining, _publish_fields = collect_target(models, db, uid, key, scope=args.scope)
    summary = {
        "mode": "apply" if args.apply else "dry_run",
        "scope": args.scope,
        "matched": len(ids),
        "changed": len(ids) if args.apply else 0,
        "remaining_matches": len(remaining),
        "snapshot": str(snapshot),
        "report": str(report),
    }
    print(json.dumps(summary, sort_keys=True))
    if args.apply and remaining:
        raise RuntimeError(f"Verification failed: {len(remaining)} unsafe Sparex products remain published.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
