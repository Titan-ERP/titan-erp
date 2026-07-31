from __future__ import annotations

import argparse
import csv
import html
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "sparex" / "pricing"


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
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read().decode("utf-8", "ignore")


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    value = html.unescape(value)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def page_count(document: str) -> int:
    pages = [int(match.group(1)) for match in re.finditer(r"/parts-by-brand/sparex/all-categories/page/(\d+)", document)]
    return max(pages) if pages else 1


def parse_page(document: str, url: str) -> list[dict[str, str]]:
    records: dict[str, dict[str, str]] = {}
    # The page has repeated listing cards. This forgiving regex works against the text-heavy HTML from EquipmentFacts/ELS pages.
    for match in re.finditer(
        r"SPAREX\s+Part\s+#\s*(S\.\d+)\s*(?P<body>.{0,3500}?)(?:Price:\s*\$|\$)\s*(?P<price>[0-9][0-9,]*(?:\.\d{2})?)",
        document,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        sku = match.group(1).upper()
        body = clean_text(match.group("body"))
        price = match.group("price").replace(",", "")
        name = body
        name = re.sub(r"^[-:\s]+", "", name)
        name = re.split(r"\bPrice\b|\bLocation\b|\bStock\b", name, maxsplit=1, flags=re.IGNORECASE)[0].strip(" -")
        if sku not in records:
            records[sku] = {
                "Internal Reference": sku,
                "Evidence Price": price,
                "Evidence URL": url,
                "Evidence Source": "Lowe & Young",
                "Evidence Name": name[:240],
                "Evidence Category": "Sparex all categories",
                "Harvested At": datetime.now().isoformat(timespec="seconds"),
                "Notes": "Exact Sparex SKU public US dealer listing.",
            }
    return list(records.values())


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest public Sparex SKU prices from Lowe & Young listing pages.")
    parser.add_argument("--start-page", type=int, default=1)
    parser.add_argument("--end-page", type=int, default=0, help="0 means auto-detect from page 1.")
    parser.add_argument("--sleep", type=float, default=0.4)
    args = parser.parse_args()

    PRICING_DIR.mkdir(parents=True, exist_ok=True)
    first_url = "https://www.loweandyoung.com/parts-by-brand/sparex/all-categories/page/1"
    first_doc = fetch(first_url)
    end_page = args.end_page or page_count(first_doc)
    print(f"Detected end page: {end_page}", flush=True)

    all_records: dict[str, dict[str, str]] = {}
    for page in range(args.start_page, end_page + 1):
        url = f"https://www.loweandyoung.com/parts-by-brand/sparex/all-categories/page/{page}"
        try:
            document = first_doc if page == 1 else fetch(url)
            rows = parse_page(document, url)
            for row in rows:
                all_records[row["Internal Reference"]] = row
            print(f"page={page} rows={len(rows)} total={len(all_records)}", flush=True)
        except Exception as exc:
            print(f"page={page} failed: {exc}", flush=True)
        if args.sleep:
            time.sleep(args.sleep)

    output = PRICING_DIR / f"lowe_young_sparex_price_evidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    with output.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(sorted(all_records.values(), key=lambda row: row["Internal Reference"]))
    print(f"Output: {output}")
    print(f"Rows: {len(all_records)}")


if __name__ == "__main__":
    main()
