from __future__ import annotations

import os
import sys
import xmlrpc.client
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"


def load_env() -> None:
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise SystemExit(f"Missing required setting: {name}")
    return value


def execute(models, db: str, uid: int, api_key: str, model: str, method: str, args: list[Any], kwargs: dict[str, Any] | None = None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def xmlid_id(models, db: str, uid: int, api_key: str, xmlid: str) -> int | None:
    rows = execute(
        models,
        db,
        uid,
        api_key,
        "ir.model.data",
        "search_read",
        [[("module", "=", xmlid.split(".", 1)[0]), ("name", "=", xmlid.split(".", 1)[1])]],
        {"fields": ["res_id"], "limit": 1},
    )
    return int(rows[0]["res_id"]) if rows else None


def main() -> int:
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Odoo authentication failed")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    fields_info = execute(
        models,
        db,
        uid,
        api_key,
        "southern.parts.catalog.sync",
        "fields_get",
        [],
        {"attributes": ["selection"]},
    )
    mode_selection = dict(fields_info.get("mode", {}).get("selection", []))
    sync_mode = "sparex_dealer_sync" if "sparex_dealer_sync" in mode_selection else "evidence_review"
    sync_name = "Sparex Dealer Website Source Sync"

    sync_rows = execute(
        models,
        db,
        uid,
        api_key,
        "southern.parts.catalog.sync",
        "search_read",
        [[("name", "=", sync_name)]],
        {"fields": ["id", "name", "mode", "active", "sequence", "batch_size", "state"], "limit": 1},
    )
    sync_values = {
        "name": sync_name,
        "sequence": 5,
        "active": True,
        "mode": sync_mode,
        "batch_size": 50,
        "state": "idle",
    }
    if sync_rows:
        sync_id = int(sync_rows[0]["id"])
        execute(models, db, uid, api_key, "southern.parts.catalog.sync", "write", [[sync_id], sync_values])
        sync_status = "updated"
    else:
        sync_id = execute(models, db, uid, api_key, "southern.parts.catalog.sync", "create", [sync_values])
        sync_status = "created"

    action_id = xmlid_id(models, db, uid, api_key, "southern_parts_intelligence.action_southern_parts_catalog_sync")
    parent_id = xmlid_id(models, db, uid, api_key, "stock.menu_stock_config_settings")
    menu_status = "skipped"
    menu_id = None
    if action_id and parent_id:
        menu_rows = execute(
            models,
            db,
            uid,
            api_key,
            "ir.ui.menu",
            "search_read",
            [[("name", "=", "Sparex Dealer Sync"), ("parent_id", "=", parent_id)]],
            {"fields": ["id", "name", "parent_id", "action"], "limit": 1},
        )
        menu_values = {
            "name": "Sparex Dealer Sync",
            "parent_id": parent_id,
            "action": f"ir.actions.act_window,{action_id}",
            "sequence": 91,
            "active": True,
        }
        if menu_rows:
            menu_id = int(menu_rows[0]["id"])
            execute(models, db, uid, api_key, "ir.ui.menu", "write", [[menu_id], menu_values])
            menu_status = "updated"
        else:
            menu_id = execute(models, db, uid, api_key, "ir.ui.menu", "create", [menu_values])
            menu_status = "created"

    crons = execute(
        models,
        db,
        uid,
        api_key,
        "ir.cron",
        "search_read",
        [[("code", "=", "model._cron_run_active_syncs()")]],
        {"fields": ["id", "name", "active", "interval_number", "interval_type"]},
    )

    print(
        {
            "sync_job": sync_status,
            "sync_id": sync_id,
            "sync_mode": sync_mode,
            "sparex_mode_available": "sparex_dealer_sync" in mode_selection,
            "inventory_menu": menu_status,
            "menu_id": menu_id,
            "catalog_crons": crons,
        }
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
