from __future__ import annotations

import argparse
import csv
import html
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"
BASE_URL = "https://lindstromequipment.com"


FIELDNAMES = [
    "Internal Reference",
    "Evidence Price",
    "Evidence URL",
    "Evidence Source",
    "Evidence Name",
    "Evidence Category",
    "Harvested At",
    "Notes",
]


def fetch(url: str) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", "ignore")


def clean_text(value: str) -> str:
    value = re.sub(r"<script\b.*?</script>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<style\b.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def absolute_url(url: str) -> str:
    return urllib.parse.urljoin(BASE_URL, html.unescape(url))


def page_count(document: str) -> int:
    pages = [int(match.group(1)) for match in re.finditer(r"[?&]page=(\d+)", html.unescape(document))]
    return max(pages) if pages else 1


def parse_json_ld(document: str, page_url: str) -> list[dict[str, str]]:
    rows = []
    # BigCommerce emits useful product snippets in HTML. This parser intentionally works from rendered text fragments
    # too, because exact JSON-LD shape varies by theme.
    for block_match in re.finditer(r"(<article\b.*?</article>)", document, flags=re.I | re.S):
        block = block_match.group(1)
        text = clean_text(block)
        sku_match = re.search(r"\bS\.\d+\b", text)
        price_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.\d{2})?)", text)
        if not sku_match or not price_match:
            continue
        link_match = re.search(r'href="([^"]+)"', block, flags=re.I)
        title_match = re.search(r'class="[^"]*card-title[^"]*"[^>]*>\s*<a[^>]*>(.*?)</a>', block, flags=re.I | re.S)
        name = clean_text(title_match.group(1)) if title_match else text[:240]
        sku = sku_match.group(0).upper()
        rows.append(
            {
                "Internal Reference": sku,
                "Evidence Price": price_match.group(1).replace(",", ""),
                "Evidence URL": absolute_url(link_match.group(1)) if link_match else page_url,
                "Evidence Source": "Lindstrom Equipment",
                "Evidence Name": name[:240],
                "Evidence Category": "Sparex listing",
                "Harvested At": datetime.now().isoformat(timespec="seconds"),
                "Notes": "Exact Sparex SKU public US dealer listing.",
            }
        )
    if rows:
        return rows

    # Fallback: find each product heading and nearby price in the raw document.
    records: dict[str, dict[str, str]] = {}
    for match in re.finditer(r"(Sparex\s+S\.\d+.{0,900}?\$\s*[0-9][0-9,]*(?:\.\d{2})?)", document, flags=re.I | re.S):
        snippet = match.group(1)
        text = clean_text(snippet)
        sku_match = re.search(r"\bS\.\d+\b", text)
        price_match = re.search(r"\$\s*([0-9][0-9,]*(?:\.\d{2})?)", text)
        if not sku_match or not price_match:
            continue
        sku = sku_match.group(0).upper()
        records[sku] = {
            "Internal Reference": sku,
            "Evidence Price": price_match.group(1).replace(",", ""),
            "Evidence URL": page_url,
            "Evidence Source": "Lindstrom Equipment",
            "Evidence Name": text[:240],
            "Evidence Category": "Sparex listing",
            "Harvested At": datetime.now().isoformat(timespec="seconds"),
            "Notes": "Exact Sparex SKU public US dealer listing.",
        }
    return list(records.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest public Sparex SKU prices from Lindstrom Equipment listing pages.")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=0, help="0 means auto-detect from page 1.")
    parser.add_argument("--sleep", type=float, default=0.4)
    args = parser.parse_args()

    PRICING_DIR.mkdir(parents=True, exist_ok=True)
    first_url = f"{BASE_URL}/sparex/?page=1"
    first_doc = fetch(first_url)
    end_page = args.end_page or page_count(first_doc)
    print(f"Detected end page: {end_page}", flush=True)

    all_records: dict[str, dict[str, str]] = {}
    for page in range(args.start_page, end_page + 1):
        url = f"{BASE_URL}/sparex/?page={page}"
        try:
            document = first_doc if page == 1 else fetch(url)
            rows = parse_json_ld(document, url)
            for row in rows:
                all_records[row["Internal Reference"]] = row
            print(f"page={page} rows={len(rows)} total={len(all_records)}", flush=True)
        except Exception as exc:
            print(f"page={page} failed: {exc}", flush=True)
        if args.sleep:
            time.sleep(args.sleep)

    output = PRICING_DIR / f"lindstrom_sparex_price_evidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(sorted(all_records.values(), key=lambda row: row["Internal Reference"]))
    print(f"Output: {output}")
    print(f"Rows: {len(all_records)}")


if __name__ == "__main__":
    main()
