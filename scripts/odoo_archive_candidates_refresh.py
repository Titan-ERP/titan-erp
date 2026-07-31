from __future__ import annotations

import csv
import os
import re
import xmlrpc.client
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
PRODUCT_MASTER = ROOT / "odoo_imports" / "product_master"
ARCHIVE_IMPORT = PRODUCT_MASTER / "import_ready" / "odoo_archive_products_set_inactive.csv"
REPORT_DIR = PRODUCT_MASTER / "review_reports"
REFRESHED = REPORT_DIR / "odoo_archive_candidates_refreshed.csv"
PROTECTED = REPORT_DIR / "odoo_archive_candidates_protected_after_cleanup.csv"

RESCUED_NAME_PATTERNS = [
    r"\bNAPA Gold\b",
    r"\bXtreme\b",
    r"\bKomatsu\b",
    r"\bKubota\b",
    r"\bBaldwin\b",
    r"\bSparex\b",
    r"\bCarquest\b",
    r"\bShell Rotella\b",
    r"\bWeasler\b",
    r"\bHowse\b",
    r"\bOil Seal\b",
    r"\bTop Carrier Roller\b",
    r"\bFuel/Water Separator\b",
    r"\bAir Filter\b",
    r"\bFuel Filter\b",
    r"\bEngine Oil Filter\b",
    r"\bHydraulic Return Filter\b",
    r"\bRotary Cutter",
    r"\bLinch Pin\b",
]


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path: Path, rows: list[dict[str, object]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def split_external_id(external_id: str):
    if "." not in external_id:
        return None
    module, name = external_id.split(".", 1)
    return (module, name) if module and name else None


def resolve_external_ids(models, db, uid, api_key, model_name: str, external_ids: list[str]) -> dict[str, int]:
    by_module = defaultdict(set)
    for external_id in external_ids:
        part = split_external_id(external_id)
        if part:
            by_module[part[0]].add(part[1])
    resolved = {}
    for module, names in by_module.items():
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "ir.model.data",
            "search_read",
            [[("module", "=", module), ("name", "in", sorted(names)), ("model", "=", model_name)]],
            {"fields": ["module", "name", "res_id"], "limit": len(names) + 20},
        )
        for row in rows:
            resolved[f"{row['module']}.{row['name']}"] = row["res_id"]
    return resolved


def rel_name(value):
    return value[1] if isinstance(value, list) and len(value) > 1 else ""


def is_rescued_name(name: str) -> bool:
    return any(re.search(pattern, name, flags=re.IGNORECASE) for pattern in RESCUED_NAME_PATTERNS)


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

    archive_rows = read_csv(ARCHIVE_IMPORT)
    external_ids = [row["ID"] for row in archive_rows if row.get("ID")]
    id_map = resolve_external_ids(models, db, uid, api_key, "product.template", external_ids)
    products = execute(
        models,
        db,
        uid,
        api_key,
        "product.template",
        "search_read",
        [[("id", "in", list(id_map.values())), ("active", "=", True)]],
        {"fields": ["id", "default_code", "name", "categ_id", "standard_price", "list_price"], "limit": 10000},
    )
    external_by_id = {res_id: external_id for external_id, res_id in id_map.items()}

    archive_ready = []
    protected = []
    for product in products:
        row = {
            "ID": external_by_id.get(product["id"], ""),
            "Product ID": product["id"],
            "Internal Reference": product.get("default_code") or "",
            "Name": product.get("name") or "",
            "Category": rel_name(product.get("categ_id")),
            "Cost": product.get("standard_price") or 0,
            "Sales Price": product.get("list_price") or 0,
        }
        if is_rescued_name(row["Name"]):
            row["Decision"] = "Protected"
            row["Reason"] = "Name now looks like a researched/cleaned part; do not archive automatically."
            protected.append(row)
        else:
            row["Decision"] = "Archive"
            row["Reason"] = "Still active and still listed in non-protected archive candidate import."
            archive_ready.append(row)

    fields = ["ID", "Product ID", "Internal Reference", "Name", "Category", "Cost", "Sales Price", "Decision", "Reason"]
    write_csv(REFRESHED, archive_ready, fields)
    write_csv(PROTECTED, protected, fields)
    print(f"Archive ready: {len(archive_ready)}")
    print(f"Protected after cleanup: {len(protected)}")
    print(f"Ready report: {REFRESHED}")
    print(f"Protected report: {PROTECTED}")


if __name__ == "__main__":
    main()
