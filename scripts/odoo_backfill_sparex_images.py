from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import socket
import sys
import time
import urllib.request
import xmlrpc.client
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SPAREX_DIR = ROOT / "odoo_imports" / "product_master" / "sparex"


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


def clean_sku(value: str) -> str:
    value = (value or "").strip().upper()
    if value.startswith("S."):
        return value
    return value


def has_binary(value: Any) -> bool:
    if value in (False, None, ""):
        return False
    if isinstance(value, str):
        return value not in {"0", "False", "false"}
    return bool(value)


def download_image_b64(url: str, cache: dict[str, str | None]) -> str | None:
    if not url:
        return None
    if url in cache:
        return cache[url]
    try:
        request = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 Southern Equipment Odoo product image backfill"},
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            content_type = response.headers.get("Content-Type", "")
            data = response.read()
        if not data or (content_type and not content_type.lower().startswith("image/")):
            cache[url] = None
            return None
        cache[url] = base64.b64encode(data).decode("ascii")
        return cache[url]
    except Exception:
        cache[url] = None
        return None


def write_image_with_retry(models, db, uid, api_key, product_id: int, image: str, attempts: int = 3) -> bool:
    for attempt in range(1, attempts + 1):
        try:
            execute(models, db, uid, api_key, "product.template", "write", [[product_id], {"image_1920": image}])
            return True
        except (xmlrpc.client.ProtocolError, TimeoutError, socket.timeout):
            if attempt == attempts:
                return False
            time.sleep(5 * attempt)
    return False


def records_from_json(path: Path) -> list[dict[str, str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    rows = []
    for record in data:
        product = record.get("product", {})
        sku = clean_sku(product.get("internal_reference") or record.get("sku") or "")
        image_url = record.get("image_url") or product.get("image_url") or ""
        if sku and image_url:
            rows.append({"sku": sku, "image_url": image_url, "source_json": str(path)})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill Odoo product photos from Sparex harvested JSON files.")
    parser.add_argument("json_paths", nargs="*", type=Path)
    parser.add_argument(
        "--sku-file",
        type=Path,
        help="CSV containing Internal Reference or sku values to target before offset/limit are applied.",
    )
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only-missing", action="store_true", default=True)
    parser.add_argument("--progress-every", type=int, default=100)
    args = parser.parse_args()

    socket.setdefaulttimeout(60)
    load_env()
    json_paths = args.json_paths or sorted(SPAREX_DIR.glob("sparex_agent_*_listing_skeleton.json"))
    candidate_rows = []
    for path in json_paths:
        candidate_rows.extend(records_from_json(path))
    by_sku = {}
    for row in candidate_rows:
        by_sku.setdefault(row["sku"], row)
    rows = list(by_sku.values())
    if args.sku_file:
        with args.sku_file.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            wanted = []
            for source_row in reader:
                sku = clean_sku(source_row.get("Internal Reference") or source_row.get("sku") or "")
                if sku:
                    wanted.append(sku)
        wanted_set = set(wanted)
        rows_by_sku = {row["sku"]: row for row in rows}
        rows = [rows_by_sku[sku] for sku in wanted if sku in wanted_set and sku in rows_by_sku]
    if args.offset:
        rows = rows[args.offset :]
    if args.limit:
        rows = rows[: args.limit]

    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    existing = []
    for sku_chunk in chunks([row["sku"] for row in rows], 300):
        existing.extend(
            execute(
                models,
                db,
                uid,
                api_key,
                "product.template",
                "search_read",
                [[("default_code", "in", sku_chunk)]],
                {"fields": ["id", "default_code", "image_1920"], "context": {"active_test": False, "bin_size": True}},
            )
        )
    existing_by_sku = {row["default_code"]: row for row in existing}
    image_cache: dict[str, str | None] = {}
    results = []
    for index, row in enumerate(rows, start=1):
        product = existing_by_sku.get(row["sku"])
        if not product:
            results.append({**row, "Status": "Product Not Found", "Product ID": ""})
        elif args.only_missing and has_binary(product.get("image_1920")):
            results.append({**row, "Status": "Already Had Image", "Product ID": product["id"]})
        else:
            image = download_image_b64(row["image_url"], image_cache)
            if not image:
                results.append({**row, "Status": "Image Download Failed", "Product ID": product["id"]})
            else:
                if write_image_with_retry(models, db, uid, api_key, product["id"], image):
                    results.append({**row, "Status": "Image Loaded", "Product ID": product["id"]})
                else:
                    results.append({**row, "Status": "Image Write Failed", "Product ID": product["id"]})
        if args.progress_every and index % args.progress_every == 0:
            loaded = sum(1 for result in results if result["Status"] == "Image Loaded")
            failed = sum(1 for result in results if result["Status"] == "Image Download Failed")
            write_failed = sum(1 for result in results if result["Status"] == "Image Write Failed")
            skipped = sum(1 for result in results if result["Status"] == "Already Had Image")
            print(
                f"Processed {index}/{len(rows)} | loaded={loaded} failed={failed} write_failed={write_failed} already={skipped}",
                flush=True,
            )

    result_path = SPAREX_DIR / f"sparex_image_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with result_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["sku", "Product ID", "Status", "image_url", "source_json"])
        writer.writeheader()
        writer.writerows(results)

    counts = {}
    for row in results:
        counts[row["Status"]] = counts.get(row["Status"], 0) + 1
    print(f"Candidates: {len(rows)}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"Results: {result_path}")


if __name__ == "__main__":
    try:
        main()
    except xmlrpc.client.Fault as exc:
        print(f"Odoo XML-RPC fault: {exc}", file=sys.stderr)
        raise SystemExit(1)
