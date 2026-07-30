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
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"


def fetch(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Southern Equipment retail pricing research",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="replace")


def clean_text(value: str) -> str:
    text = html.unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def clean_sparex_sku(value: str) -> str:
    value = re.sub(r"\s+", "", (value or "").strip().upper())
    match = re.search(r"S\.?(\d+)", value)
    return f"S.{match.group(1)}" if match else value


def farmingparts_search_url(sku: str) -> str:
    return "https://farmingparts.com/search?" + urllib.parse.urlencode({"q": sku})


def parse_farmingparts_prices(source: str, query_sku: str, source_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    query_sku = clean_sparex_sku(query_sku)
    meta_match = re.search(r"\bvar\s+meta\s*=\s*(\{.*?\});", source, flags=re.S)
    if not meta_match:
        return rows
    try:
        payload = json.loads(meta_match.group(1))
    except json.JSONDecodeError:
        return rows
    for product in payload.get("products", []):
        product_title = product.get("title") or ""
        product_url = "https://farmingparts.com/products/" + str(product.get("handle") or "").strip()
        for variant in product.get("variants", []):
            variant_sku = clean_sparex_sku(variant.get("sku") or "")
            if variant_sku != query_sku:
                continue
            cents = variant.get("price")
            if cents in (None, ""):
                continue
            rows.append(
                {
                    "Internal Reference": variant_sku,
                    "Observed Retail Price": round(float(cents) / 100.0, 2),
                    "Currency": "GBP",
                    "Source": "Farming Parts",
                    "Source URL": product_url,
                    "Source Search URL": source_url,
                    "Title": clean_text(product_title or variant.get("name") or ""),
                    "Confidence": "0.92",
                    "Notes": "Exact SKU match in Shopify product metadata. UK retail price; tax/shipping/currency conversion requires review before Odoo update.",
                }
            )
    return rows


def research_farmingparts_skus(skus: list[str], delay: float, limit: int, timeout: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, sku in enumerate(skus[:limit], start=1):
        url = farmingparts_search_url(sku)
        try:
            source = fetch(url, timeout=timeout)
            found = parse_farmingparts_prices(source, sku, url)
            rows.extend(found)
            print(f"Farming Parts {index}/{min(len(skus), limit)} {sku}: {len(found)}", flush=True)
        except Exception as exc:
            print(f"Farming Parts {sku}: ERROR {exc}", flush=True)
        time.sleep(delay)
    return rows


def parse_lowe_young_page(source: str, page_url: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    pattern = re.compile(
        r'<a href="(?P<url>https://www\.loweandyoung\.com/buy-parts/SPAREX-Part-S(?P<sku>\d+)[^"]*)">(?P<title>.*?)</a>.*?'
        r'<span class="black highlightprice">Price:&nbsp;</span>.*?'
        r'<strong>\$(?P<price>[0-9,]+\.\d{2})</strong>',
        flags=re.S | re.I,
    )
    for match in pattern.finditer(source):
        rows.append(
            {
                "Internal Reference": f"S.{match.group('sku')}",
                "Observed Retail Price": float(match.group("price").replace(",", "")),
                "Currency": "USD",
                "Source": "Lowe & Young",
                "Source URL": html.unescape(match.group("url")),
                "Source Search URL": page_url,
                "Title": clean_text(match.group("title")),
                "Confidence": "0.9",
                "Notes": "Public USD dealer page price. Use as retail benchmark, not automatic Odoo price without margin review.",
            }
        )
    return rows


def research_lowe_young_pages(start_page: int, pages: int, delay: float, timeout: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page in range(start_page, start_page + pages):
        url = f"https://www.loweandyoung.com/parts-by-brand/sparex/all-categories/page/{page}"
        try:
            source = fetch(url, timeout=timeout)
            found = parse_lowe_young_page(source, url)
            rows.extend(found)
            print(f"Lowe & Young page {page}: {len(found)}", flush=True)
        except Exception as exc:
            print(f"Lowe & Young page {page}: ERROR {exc}", flush=True)
        time.sleep(delay)
    return rows


def read_skus(path: Path | None) -> list[str]:
    if not path:
        return []
    values: list[str] = []
    if path.suffix.lower() == ".csv":
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                for key in ("Internal Reference", "internal_reference", "SKU", "sku", "default_code"):
                    if row.get(key):
                        values.append(clean_sparex_sku(row[key]))
                        break
    else:
        values = [clean_sparex_sku(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return list(dict.fromkeys(value for value in values if value.startswith("S.")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Research public retail prices for parts without writing Odoo prices.")
    parser.add_argument("--sku-file", type=Path)
    parser.add_argument("--sku", action="append", default=[])
    parser.add_argument("--farmingparts-limit", type=int, default=25)
    parser.add_argument("--lowe-young-pages", type=int, default=5)
    parser.add_argument("--lowe-young-start-page", type=int, default=1)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=12)
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    skus = list(dict.fromkeys([clean_sparex_sku(sku) for sku in args.sku] + read_skus(args.sku_file)))
    if not skus:
        skus = ["S.40563", "S.40564", "S.40560", "S.40565", "S.40561", "S.40567", "S.40566", "S.40568", "S.40569"]

    rows = []
    rows.extend(research_farmingparts_skus(skus, args.delay, args.farmingparts_limit, args.timeout))
    rows.extend(research_lowe_young_pages(args.lowe_young_start_page, args.lowe_young_pages, args.delay, args.timeout))

    seen = set()
    deduped = []
    for row in rows:
        key = (row["Internal Reference"], row["Source"], row["Observed Retail Price"], row["Currency"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or f"web_retail_price_research_{stamp}"
    csv_path = OUT_DIR / f"{run_name}.csv"
    fieldnames = [
        "Internal Reference",
        "Observed Retail Price",
        "Currency",
        "Source",
        "Source URL",
        "Source Search URL",
        "Title",
        "Confidence",
        "Notes",
    ]
    with csv_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(deduped)

    print(f"Wrote {csv_path}")
    print(f"Rows: {len(deduped)}")
    print(f"Farming Parts rows: {sum(1 for row in deduped if row['Source'] == 'Farming Parts')}")
    print(f"Lowe & Young rows: {sum(1 for row in deduped if row['Source'] == 'Lowe & Young')}")


if __name__ == "__main__":
    main()
