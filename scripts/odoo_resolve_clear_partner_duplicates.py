import os
import sys
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"

DUPLICATE_RESOLUTIONS = [
    {"key": "Michael McCormick", "keep_id": 199, "archive_id": 387},
    {"key": "Rays Used Equipment", "keep_id": 1640, "archive_id": 2017},
]
REFERENCE_MODELS = ["account.move", "sale.order", "purchase.order", "account.move.line"]


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def connect():
    load_env(ENV_PATH)
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def model_exists(models, db, uid, api_key, model):
    return bool(execute(models, db, uid, api_key, "ir.model", "search_count", [[("model", "=", model)]]))


def reference_count(models, db, uid, api_key, partner_id):
    counts = {}
    for model in REFERENCE_MODELS:
        if not model_exists(models, db, uid, api_key, model):
            counts[model] = 0
            continue
        counts[model] = execute(models, db, uid, api_key, model, "search_count", [[("partner_id", "=", partner_id)]])
    return counts


def append_unique(existing, addition):
    existing = str(existing or "")
    addition = str(addition or "")
    if not addition or addition in existing:
        return existing
    return existing + addition if existing else addition


def main():
    db, uid, api_key, models = connect()
    rows = []
    for resolution in DUPLICATE_RESOLUTIONS:
        keep_id = resolution["keep_id"]
        archive_id = resolution["archive_id"]
        refs = reference_count(models, db, uid, api_key, archive_id)
        if any(refs.values()):
            rows.append(f"Skipped {archive_id}: duplicate has references {refs}")
            continue

        keep, duplicate = execute(
            models,
            db,
            uid,
            api_key,
            "res.partner",
            "read",
            [[keep_id, archive_id]],
            {"fields": ["id", "name", "active", "ref", "comment", "customer_rank", "supplier_rank", "phone", "email"]},
        )
        updates = {}
        if not keep.get("ref") and duplicate.get("ref"):
            updates["ref"] = duplicate["ref"]
        if int(duplicate.get("customer_rank") or 0) > int(keep.get("customer_rank") or 0):
            updates["customer_rank"] = duplicate["customer_rank"]
        if int(duplicate.get("supplier_rank") or 0) > int(keep.get("supplier_rank") or 0):
            updates["supplier_rank"] = duplicate["supplier_rank"]
        merged_comment = append_unique(keep.get("comment"), duplicate.get("comment"))
        if merged_comment != str(keep.get("comment") or ""):
            updates["comment"] = merged_comment
        if not keep.get("phone") and duplicate.get("phone"):
            updates["phone"] = duplicate["phone"]
        if not keep.get("email") and duplicate.get("email"):
            updates["email"] = duplicate["email"]

        if updates:
            execute(models, db, uid, api_key, "res.partner", "write", [[keep_id], updates])
        execute(models, db, uid, api_key, "res.partner", "write", [[archive_id], {"active": False}])
        rows.append(f"Archived duplicate {archive_id} into keeper {keep_id} ({resolution['key']}); updated fields: {', '.join(sorted(updates)) or 'none'}")

    for row in rows:
        print(row)


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
