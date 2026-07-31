from __future__ import annotations

import argparse
import base64
import importlib.util
import os
import re
import socket
import urllib.request
import xmlrpc.client
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
HARVESTER_PATH = ROOT / "scripts" / "blumaq_harvest_agent.py"


def load_harvester():
    spec = importlib.util.spec_from_file_location("blumaq_harvest_agent", HARVESTER_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Could not load {HARVESTER_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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


def source_url_from_description(value: str) -> str:
    match = re.search(r"Blumaq source:\s*(https://\S+)", value or "")
    return match.group(1).strip() if match else ""


def download_image_b64(url: str) -> str | None:
    if not url:
        return None
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Southern Equipment Blumaq image backfill"})
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
        if not data or (content_type and not content_type.lower().startswith("image/")):
            return None
        return base64.b64encode(data).decode("ascii")
    except Exception:
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill missing Odoo images for BLQ Blumaq products from stored public source URLs.")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--progress-every", type=int, default=25)
    args = parser.parse_args()

    socket.setdefaulttimeout(60)
    harvester = load_harvester()
    load_env()
    url = required("ODOO_URL").rstrip("/")
    db = required("ODOO_DB")
    username = required("ODOO_USERNAME")
    api_key = required("ODOO_API_KEY")
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    ids = execute(models, db, uid, api_key, "product.template", "search", [[("default_code", "=like", "BLQ-%")]], {"limit": args.limit, "context": {"active_test": False}})
    rows = []
    for id_chunk in chunks(ids, 250):
        rows.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "read",
                [id_chunk],
                {"fields": ["id", "default_code", "name", "description_purchase", "image_1920"], "context": {"active_test": False, "bin_size": True}},
            )
        )

    checked = loaded = already = failed = no_source = no_image = 0
    for row in rows:
        checked += 1
        if has_binary(row.get("image_1920")):
            already += 1
            continue
        source_url = source_url_from_description(row.get("description_purchase") or "")
        if not source_url:
            no_source += 1
            continue
        try:
            source = harvester.fetch(source_url, 30)
            image_urls = harvester.extract_images(source, source_url)
        except Exception:
            failed += 1
            continue
        if not image_urls:
            no_image += 1
            continue
        image_data = download_image_b64(image_urls[0])
        if not image_data:
            failed += 1
            continue
        execute(models, db, uid, api_key, "product.template", "write", [[row["id"]], {"image_1920": image_data}])
        loaded += 1
        if args.progress_every and checked % args.progress_every == 0:
            print(f"Checked={checked} loaded={loaded} already={already} failed={failed} no_image={no_image}", flush=True)

    print(f"Checked: {checked}")
    print(f"Already had image: {already}")
    print(f"Loaded: {loaded}")
    print(f"No source URL: {no_source}")
    print(f"No image on page: {no_image}")
    print(f"Failed: {failed}")


if __name__ == "__main__":
    main()
