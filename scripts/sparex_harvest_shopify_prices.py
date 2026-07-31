from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


FIELDNAMES = [
    "Internal Reference",
    "Evidence Price",
    "Evidence Currency",
    "Evidence URL",
    "Evidence Source",
    "Evidence Name",
    "Evidence Category",
    "Harvested At",
    "Notes",
]


def fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8", "ignore"))


def clean_text(value: str) -> str:
    value = html.unescape(str(value or ""))
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def clean_sku(value: str) -> str:
    text = str(value or "").upper()
    match = re.search(r"\bS[.\-\s]*(\d{1,8})\b", text)
    if match:
        return f"S.{match.group(1)}"
    return ""


def product_sku(product: dict) -> str:
    candidates = [
        product.get("title", ""),
        product.get("handle", ""),
        product.get("body_html", ""),
    ]
    for variant in product.get("variants", []) or []:
        candidates.extend([variant.get("sku", ""), variant.get("title", "")])
    for value in candidates:
        sku = clean_sku(str(value))
        if sku:
            return sku
    return ""


def product_price(product: dict) -> str:
    prices = []
    for variant in product.get("variants", []) or []:
        try:
            price = float(variant.get("price") or 0)
            if price > 0:
                prices.append(price)
        except ValueError:
            continue
    if not prices:
        return ""
    return f"{min(prices):.2f}"


def product_url(base_url: str, product: dict) -> str:
    handle = product.get("handle", "")
    if handle:
        return urllib.parse.urljoin(base_url.rstrip("/") + "/", f"products/{handle}")
    return base_url


def harvest(
    base_url: str,
    source_name: str,
    currency: str,
    collection: str,
    start_page: int,
    max_pages: int,
    sleep: float,
    continue_on_error: bool,
) -> list[dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    if collection:
        endpoint_template = base_url.rstrip("/") + f"/collections/{collection}/products.json?limit=250&page={{page}}"
    else:
        endpoint_template = base_url.rstrip("/") + "/products.json?limit=250&page={page}"

    for page in range(start_page, max_pages + 1):
        url = endpoint_template.format(page=page)
        try:
            data = fetch_json(url)
        except Exception as exc:
            print(f"page={page} failed: {exc}", flush=True)
            if continue_on_error:
                continue
            break
        products = data.get("products") or []
        if not products:
            print(f"page={page} empty", flush=True)
            break
        added = 0
        for product in products:
            sku = product_sku(product)
            price = product_price(product)
            if not sku or not price:
                continue
            title = clean_text(product.get("title", ""))
            body = clean_text(product.get("body_html", ""))
            if "sparex" not in f"{title} {body} {sku}".lower():
                continue
            records[sku] = {
                "Internal Reference": sku,
                "Evidence Price": price,
                "Evidence Currency": currency,
                "Evidence URL": product_url(base_url, product),
                "Evidence Source": source_name,
                "Evidence Name": title[:240],
                "Evidence Category": "Shopify product feed",
                "Harvested At": datetime.now().isoformat(timespec="seconds"),
                "Notes": f"Exact Sparex SKU public {currency} Shopify listing. Currency is not USD unless Evidence Currency is USD.",
            }
            added += 1
        print(f"page={page} products={len(products)} added={added} total={len(records)}", flush=True)
        if sleep:
            time.sleep(sleep)
    return list(records.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest exact Sparex SKU prices from Shopify products.json feeds.")
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--source-name", required=True)
    parser.add_argument("--currency", default="GBP")
    parser.add_argument("--collection", default="")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--max-pages", type=int, default=100)
    parser.add_argument("--sleep", type=float, default=0.15)
    parser.add_argument("--continue-on-error", action="store_true")
    args = parser.parse_args()

    PRICING_DIR.mkdir(parents=True, exist_ok=True)
    rows = harvest(
        args.base_url,
        args.source_name,
        args.currency,
        args.collection,
        args.start_page,
        args.max_pages,
        args.sleep,
        args.continue_on_error,
    )
    slug = re.sub(r"[^a-z0-9]+", "_", args.source_name.lower()).strip("_")
    output = PRICING_DIR / f"{slug}_sparex_price_evidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(sorted(rows, key=lambda row: row["Internal Reference"]))
    print(f"Output: {output}")
    print(f"Rows: {len(rows)}")


if __name__ == "__main__":
    main()
