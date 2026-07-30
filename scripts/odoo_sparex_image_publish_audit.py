from __future__ import annotations

import csv
import os
import xmlrpc.client
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
REPORT_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "run_reports"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def chunks(values: list[Any], size: int) -> list[list[Any]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def has_binary(value: Any) -> bool:
    if value in (False, None, ""):
        return False
    if isinstance(value, str):
        return value not in {"0", "False", "false"}
    return bool(value)


def main() -> None:
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    ids = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search",
        [[("active", "=", True), ("sale_ok", "=", True), ("default_code", "=ilike", "S.%")]],
        {"context": {"active_test": False}},
    )
    rows = []
    counts = Counter()
    for id_chunk in chunks(ids, 500):
        products = execute(
            models,
            db,
            uid,
            api_key,
            "product.template",
            "read",
            [id_chunk],
            {
                "fields": ["id", "default_code", "name", "image_1920", "is_published", "website_published"],
                "context": {"active_test": False, "bin_size": True},
            },
        )
        for product in products:
            has_image = has_binary(product.get("image_1920"))
            is_published = bool(product.get("is_published")) or bool(product.get("website_published"))
            counts["total"] += 1
            counts["with_image" if has_image else "missing_image"] += 1
            counts["published" if is_published else "unpublished"] += 1
            if not has_image or not is_published:
                rows.append(
                    {
                        "Product ID": product["id"],
                        "Internal Reference": product.get("default_code") or "",
                        "Name": product.get("name") or "",
                        "Published": "Yes" if is_published else "No",
                        "Has Image": "Yes" if has_image else "No",
                    }
                )

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORT_DIR / "sparex_image_publish_gaps.csv"
    with report_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["Product ID", "Internal Reference", "Name", "Published", "Has Image"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Sparex active sale products: {counts['total']}")
    print(f"Published: {counts['published']}")
    print(f"Unpublished: {counts['unpublished']}")
    print(f"With image: {counts['with_image']}")
    print(f"Missing image: {counts['missing_image']}")
    print(f"Gaps report: {report_path}")


if __name__ == "__main__":
    main()
