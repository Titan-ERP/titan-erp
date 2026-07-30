from __future__ import annotations

import argparse
import json
import xmlrpc.client
from collections import defaultdict
from pathlib import Path
from typing import Any

import odoo_import_parts_intelligence_json as base


def read_payload(path: Path) -> list[dict[str, Any]]:
    return base.as_records(json.loads(path.read_text(encoding="utf-8")))


def clean(value: Any) -> str:
    return str(value or "").strip()


def list_value(record: dict[str, Any], *keys: str) -> list[dict[str, Any]]:
    return base.list_value(record, *keys)


def product_map(models, db, uid, api_key, records: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, str]]:
    skus = sorted(
        {
            base.clean_sku(str(base.first_value(record, "product.internal_reference", "internal_reference", "sku", "default_code")))
            for record in records
        }
    )
    skus = [sku for sku in skus if sku]
    rows = base.execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_read",
        [[("default_code", "in", skus)]],
        {"fields": ["id", "default_code"], "limit": len(skus) or 1},
    )
    return {row["default_code"]: row["id"] for row in rows}, {sku: "product_not_found" for sku in skus if sku not in {row["default_code"] for row in rows}}


def existing_keys(models, db, uid, api_key, model: str, domain: list[Any], fields: list[str], key_fn) -> set[tuple[Any, ...]]:
    rows = base.execute(models, db, uid, api_key, model, "search_read", [domain], {"fields": fields, "limit": 100000})
    return {key_fn(row) for row in rows}


def ensure_makes_models(models, db, uid, api_key, fitment_values: list[dict[str, Any]], apply: bool) -> tuple[dict[str, int], dict[tuple[str, str], int], dict[str, int]]:
    stats: dict[str, int] = defaultdict(int)
    makes = sorted({clean(item.get("make")) for item in fitment_values if clean(item.get("make"))})
    make_rows = base.execute(
        models,
        db,
        uid,
        api_key,
        "southern.parts.make",
        "search_read",
        [[("name", "in", makes)]],
        {"fields": ["id", "name"], "limit": max(len(makes), 1)},
    )
    make_by_name = {row["name"]: row["id"] for row in make_rows}
    missing_makes = [{"name": name} for name in makes if name not in make_by_name]
    if missing_makes and apply:
        base.execute(models, db, uid, api_key, "southern.parts.make", "create", [missing_makes])
        stats["makes_created"] += len(missing_makes)
        make_rows = base.execute(
            models,
            db,
            uid,
            api_key,
            "southern.parts.make",
            "search_read",
            [[("name", "in", makes)]],
            {"fields": ["id", "name"], "limit": max(len(makes), 1)},
        )
        make_by_name = {row["name"]: row["id"] for row in make_rows}

    model_pairs = sorted(
        {
            (clean(item.get("make")), clean(item.get("model")))
            for item in fitment_values
            if clean(item.get("make")) and clean(item.get("model")) and clean(item.get("make")) in make_by_name
        }
    )
    model_by_pair: dict[tuple[str, str], int] = {}
    if make_by_name:
        model_rows = base.execute(
            models,
            db,
            uid,
            api_key,
            "southern.parts.model",
            "search_read",
            [[("make_id", "in", list(make_by_name.values()))]],
            {"fields": ["id", "name", "make_id"], "limit": 100000},
        )
        make_name_by_id = {value: key for key, value in make_by_name.items()}
        for row in model_rows:
            model_by_pair[(make_name_by_id[row["make_id"][0]], row["name"])] = row["id"]
    missing_models = [
        {"make_id": make_by_name[make], "name": model}
        for make, model in model_pairs
        if (make, model) not in model_by_pair
    ]
    if missing_models and apply:
        base.execute(models, db, uid, api_key, "southern.parts.model", "create", [missing_models])
        stats["models_created"] += len(missing_models)
        model_rows = base.execute(
            models,
            db,
            uid,
            api_key,
            "southern.parts.model",
            "search_read",
            [[("make_id", "in", list(make_by_name.values()))]],
            {"fields": ["id", "name", "make_id"], "limit": 100000},
        )
        make_name_by_id = {value: key for key, value in make_by_name.items()}
        model_by_pair = {(make_name_by_id[row["make_id"][0]], row["name"]): row["id"] for row in model_rows}
    elif missing_models:
        stats["models"] += len(missing_models)
        next_model_id = -1
        for make, model in model_pairs:
            if (make, model) not in model_by_pair:
                model_by_pair[(make, model)] = next_model_id
                next_model_id -= 1
    if missing_makes and not apply:
        stats["makes"] += len(missing_makes)
        next_id = -1
        for item in missing_makes:
            make_by_name[item["name"]] = next_id
            next_id -= 1
        missing_pairs = [
            (clean(item.get("make")), clean(item.get("model")))
            for item in fitment_values
            if clean(item.get("make")) in make_by_name and clean(item.get("model"))
        ]
        next_model_id = -1
        for pair in sorted(set(missing_pairs)):
            if pair not in model_by_pair:
                model_by_pair[pair] = next_model_id
                next_model_id -= 1
    return make_by_name, model_by_pair, stats


def unique_rows(rows: list[dict[str, Any]], key_fn) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for row in rows:
        key = key_fn(row)
        if key in seen:
            continue
        seen.add(key)
        unique.append(row)
    return unique


def main() -> None:
    parser = argparse.ArgumentParser(description="Fast bulk import for southern Parts Intelligence JSON payloads.")
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    records = read_payload(args.json_path)
    db, uid, api_key, models = base.connect()
    base.ensure_model_installed(models, db, uid, api_key)
    products, missing = product_map(models, db, uid, api_key, records)

    per_record_source: dict[int, tuple[str, str, str]] = {}
    specs: list[dict[str, Any]] = []
    oems: list[dict[str, Any]] = []
    fitments: list[dict[str, Any]] = []
    catalogs: list[dict[str, Any]] = []
    related: list[dict[str, Any]] = []
    barcodes: list[dict[str, Any]] = []

    for record in records:
        sku = base.clean_sku(str(base.first_value(record, "product.internal_reference", "internal_reference", "sku", "default_code")))
        product_id = products.get(sku)
        if not product_id:
            continue
        source = base.first_value(record, "source.vendor", "source_name") or "Unknown"
        source_url = base.first_value(record, "source.url", "source_url")
        enrichment_status = clean(record.get("enrichment_status") or "complete") or "complete"
        if enrichment_status not in {"none", "partial", "complete", "review"}:
            enrichment_status = "review"
        per_record_source[product_id] = (source, source_url, enrichment_status)

        for spec in list_value(record, "specifications", "product_specifications", "specs"):
            name = clean(spec.get("name") or spec.get("label"))
            value = clean(spec.get("value"))
            if name and value:
                specs.append(
                    {
                        "product_tmpl_id": product_id,
                        "group_name": spec.get("group_name") or spec.get("group") or "Specifications",
                        "name": name,
                        "value": value,
                        "unit": spec.get("unit") or "",
                        "source_name": spec.get("source_name") or source,
                        "source_url": spec.get("source_url") or source_url,
                        "confidence": float(spec.get("confidence") or 1.0),
                    }
                )
        for ref in list_value(record, "oem_references", "oem_part_numbers", "oem_refs"):
            manufacturer = clean(ref.get("manufacturer") or ref.get("make"))
            part_number = clean(ref.get("part_number") or ref.get("oem_part_number") or ref.get("value"))
            if manufacturer and part_number:
                oems.append(
                    {
                        "product_tmpl_id": product_id,
                        "manufacturer": manufacturer,
                        "oem_part_number": part_number,
                        "reference_type": ref.get("reference_type") or "oem",
                        "source_name": ref.get("source_name") or source,
                        "source_url": ref.get("source_url") or source_url,
                        "confidence": float(ref.get("confidence") or 1.0),
                    }
                )
        for fitment in list_value(record, "fitments", "suitable_for"):
            make = clean(fitment.get("make"))
            model = clean(fitment.get("model"))
            if make and model:
                fitments.append(
                    {
                        "product_tmpl_id": product_id,
                        "make": make,
                        "model": model,
                        "engine": fitment.get("engine") or "",
                        "year_from": fitment.get("year_from") or 0,
                        "year_to": fitment.get("year_to") or 0,
                        "build_list": fitment.get("build_list") or "",
                        "notes": fitment.get("notes") or "",
                        "source_name": fitment.get("source_name") or source,
                        "source_url": fitment.get("source_url") or source_url,
                        "confidence": float(fitment.get("confidence") or 1.0),
                    }
                )
        for catalog in list_value(record, "catalog_pages", "catalogs"):
            catalog_name = clean(catalog.get("catalog_name") or catalog.get("catalog"))
            page_number = clean(catalog.get("page_number") or catalog.get("page"))
            if catalog_name and page_number:
                catalogs.append(
                    {
                        "product_tmpl_id": product_id,
                        "catalog_code": catalog.get("catalog_code") or "",
                        "catalog_name": catalog_name,
                        "page_number": page_number,
                        "source_name": catalog.get("source_name") or source,
                        "source_url": catalog.get("source_url") or source_url,
                    }
                )
        for item in list_value(record, "related_parts", "related_products"):
            related_sku = base.clean_sku(clean(item.get("internal_reference") or item.get("sku") or item.get("default_code")))
            related_id = products.get(related_sku) or base.find_product(models, db, uid, api_key, related_sku)
            if related_id:
                related.append(
                    {
                        "product_tmpl_id": product_id,
                        "related_product_tmpl_id": related_id,
                        "relationship_type": item.get("relationship_type") or "related",
                        "source_name": item.get("source_name") or source,
                        "source_url": item.get("source_url") or source_url,
                        "confidence": float(item.get("confidence") or 1.0),
                        "notes": item.get("notes") or "",
                    }
                )
        for item in list_value(record, "alternate_barcodes", "barcodes"):
            code = clean(item.get("barcode") or item.get("value"))
            if code:
                barcodes.append(
                    {
                        "product_tmpl_id": product_id,
                        "barcode": code,
                        "barcode_type": item.get("barcode_type") or item.get("type") or "other",
                        "source_name": item.get("source_name") or source,
                        "source_url": item.get("source_url") or source_url,
                    }
                )

    totals: dict[str, int] = defaultdict(int)
    product_ids = list(per_record_source)
    make_by_name, model_by_pair, make_model_stats = ensure_makes_models(models, db, uid, api_key, fitments, args.apply)
    totals.update(make_model_stats)

    existing_spec = existing_keys(
        models,
        db,
        uid,
        api_key,
        "southern.parts.specification",
        [("product_tmpl_id", "in", product_ids)],
        ["product_tmpl_id", "group_name", "name", "value"],
        lambda row: (row["product_tmpl_id"][0], row.get("group_name") or "", row["name"], row["value"]),
    )
    new_specs = unique_rows([
        item for item in specs if (item["product_tmpl_id"], item.get("group_name") or "", item["name"], item["value"]) not in existing_spec
    ], lambda item: (item["product_tmpl_id"], item.get("group_name") or "", item["name"], item["value"]))
    totals["specs"] = len(new_specs)

    existing_oem = existing_keys(
        models,
        db,
        uid,
        api_key,
        "southern.parts.oem_reference",
        [("product_tmpl_id", "in", product_ids)],
        ["product_tmpl_id", "manufacturer", "oem_part_number", "reference_type"],
        lambda row: (row["product_tmpl_id"][0], row["manufacturer"], row["oem_part_number"], row["reference_type"]),
    )
    new_oems = unique_rows([
        item
        for item in oems
        if (item["product_tmpl_id"], item["manufacturer"], item["oem_part_number"], item["reference_type"]) not in existing_oem
    ], lambda item: (item["product_tmpl_id"], item["manufacturer"], item["oem_part_number"], item["reference_type"]))
    totals["oem_refs"] = len(new_oems)

    fitment_create = []
    existing_fitment = existing_keys(
        models,
        db,
        uid,
        api_key,
        "southern.parts.fitment",
        [("product_tmpl_id", "in", product_ids)],
        ["product_tmpl_id", "make_id", "model_id", "engine", "build_list"],
        lambda row: (row["product_tmpl_id"][0], row["make_id"][0], row["model_id"][0], row.get("engine") or "", row.get("build_list") or ""),
    )
    for item in fitments:
        make_name = item.pop("make")
        model_name = item.pop("model")
        make_id = make_by_name.get(make_name)
        model_id = model_by_pair.get((make_name, model_name)) if make_id else None
        if not make_id or not model_id:
            continue
        item["make_id"] = make_id
        item["model_id"] = model_id
        key = (item["product_tmpl_id"], make_id, model_id, item.get("engine") or "", item.get("build_list") or "")
        if key not in existing_fitment:
            fitment_create.append(item)
    fitment_create = unique_rows(
        fitment_create,
        lambda item: (item["product_tmpl_id"], item["make_id"], item["model_id"], item.get("engine") or "", item.get("build_list") or ""),
    )
    totals["fitments"] = len(fitment_create)

    existing_catalog = existing_keys(
        models,
        db,
        uid,
        api_key,
        "southern.parts.catalog_page",
        [("product_tmpl_id", "in", product_ids)],
        ["product_tmpl_id", "catalog_name", "page_number"],
        lambda row: (row["product_tmpl_id"][0], row["catalog_name"], row["page_number"]),
    )
    new_catalogs = unique_rows([
        item for item in catalogs if (item["product_tmpl_id"], item["catalog_name"], item["page_number"]) not in existing_catalog
    ], lambda item: (item["product_tmpl_id"], item["catalog_name"], item["page_number"]))
    totals["catalog_pages"] = len(new_catalogs)

    existing_related = existing_keys(
        models,
        db,
        uid,
        api_key,
        "southern.parts.related_product",
        [("product_tmpl_id", "in", product_ids)],
        ["product_tmpl_id", "related_product_tmpl_id", "relationship_type"],
        lambda row: (row["product_tmpl_id"][0], row["related_product_tmpl_id"][0], row["relationship_type"]),
    )
    new_related = unique_rows([
        item
        for item in related
        if (item["product_tmpl_id"], item["related_product_tmpl_id"], item["relationship_type"]) not in existing_related
    ], lambda item: (item["product_tmpl_id"], item["related_product_tmpl_id"], item["relationship_type"]))
    totals["related_parts"] = len(new_related)

    existing_barcode = existing_keys(
        models,
        db,
        uid,
        api_key,
        "southern.parts.alternate_barcode",
        [("product_tmpl_id", "in", product_ids)],
        ["product_tmpl_id", "barcode"],
        lambda row: (row["product_tmpl_id"][0], row["barcode"]),
    )
    new_barcodes = unique_rows(
        [item for item in barcodes if (item["product_tmpl_id"], item["barcode"]) not in existing_barcode],
        lambda item: (item["product_tmpl_id"], item["barcode"]),
    )
    totals["barcodes"] = len(new_barcodes)

    if args.apply:
        for product_id, (source, source_url, status) in per_record_source.items():
            base.execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "write",
                [[product_id], {"southern_source_name": source, "southern_source_url": source_url, "southern_enrichment_status": status}],
            )
        for model, rows in [
            ("southern.parts.specification", new_specs),
            ("southern.parts.oem_reference", new_oems),
            ("southern.parts.fitment", fitment_create),
            ("southern.parts.catalog_page", new_catalogs),
            ("southern.parts.related_product", new_related),
            ("southern.parts.alternate_barcode", new_barcodes),
        ]:
            if rows:
                base.execute(models, db, uid, api_key, model, "create", [rows])

    print(json.dumps({"records": len(records), "matched_products": len(products), "mode": "apply" if args.apply else "dry_run", "totals": dict(totals), "skipped": missing}, indent=2))


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}")
        raise SystemExit(1)

