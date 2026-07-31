from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PRICING_DIR = ROOT / "odoo_imports" / "product_master" / "pricing"
DEFAULT_REGISTRY = PRICING_DIR / "retail_price_source_registry.csv"


REGISTRY_FIELDS = [
    "Internal Reference",
    "Source",
    "Price URL",
    "Search URL",
    "Currency",
    "Last Observed Price",
    "Last Checked",
    "Confidence",
    "Title",
    "Active",
    "Notes",
]

DELTA_FIELDS = [
    "Internal Reference",
    "Source",
    "Price URL",
    "Previous Price",
    "Current Price",
    "Currency",
    "Changed",
    "Percent Change",
    "Status",
    "Confidence",
    "Checked At",
    "Recommendation",
    "Notes",
]


def fetch(url: str, timeout: int = 30) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Southern Equipment retail price monitor",
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


def parse_price_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def parse_shopify_meta_prices(source: str, sku: str) -> tuple[float | None, str, str]:
    sku = clean_sparex_sku(sku)
    meta_match = re.search(r"\bvar\s+meta\s*=\s*(\{.*?\});", source, flags=re.S)
    if not meta_match:
        return None, "", ""
    try:
        payload = json.loads(meta_match.group(1))
    except json.JSONDecodeError:
        return None, "", ""
    products = payload.get("products", [])
    if isinstance(payload.get("product"), dict):
        products.append(payload["product"])
    for product in products:
        title = product.get("title") or ""
        handle = product.get("handle") or ""
        for variant in product.get("variants", []):
            variant_sku = clean_sparex_sku(variant.get("sku") or "")
            if variant_sku != sku:
                continue
            cents = variant.get("price")
            if cents in (None, ""):
                continue
            product_url = f"https://farmingparts.com/products/{handle}" if handle else ""
            return round(float(cents) / 100.0, 2), clean_text(title or variant.get("name") or ""), product_url
    return None, "", ""


def parse_lowe_young_price(source: str) -> float | None:
    patterns = [
        r'<span class="black highlightprice">Price:&nbsp;</span>.*?<strong>\$(?P<price>[0-9,]+\.\d{2})</strong>',
        r'Price:\s*</span>.*?<strong>\$(?P<price>[0-9,]+\.\d{2})</strong>',
        r'\$(?P<price>[0-9,]+\.\d{2})',
    ]
    for pattern in patterns:
        match = re.search(pattern, source, flags=re.S | re.I)
        if match:
            return float(match.group("price").replace(",", ""))
    return None


def parse_lowe_young_listing_for_sku(source: str, sku: str) -> tuple[float | None, str, str]:
    clean_sku = clean_sparex_sku(sku).replace(".", "")
    blocks = re.findall(r'<div class="col-sm-3[^"]*">\s*<div class="pl-br">(.*?)</div>\s*</div>', source, flags=re.S | re.I)
    if not blocks:
        blocks = re.findall(r'(<a href="[^"]*SPAREX-Part-S[^"]+.*?</a>.*?)(?=<a href="[^"]*SPAREX-Part-S|$)', source, flags=re.S | re.I)

    for block in blocks:
        block_text = clean_text(block).upper().replace(".", "")
        if clean_sku not in block_text:
            continue
        price = parse_lowe_young_price(block)
        link_match = re.search(r'href="(?P<href>[^"]*SPAREX-Part-S[^"]+)"', block, flags=re.I)
        href = html.unescape(link_match.group("href")) if link_match else ""
        if href and href.startswith("/"):
            href = f"https://www.loweandyoung.com{href}"
        title_match = re.search(r'<a[^>]*>(?P<title>.*?)</a>', block, flags=re.S | re.I)
        title = clean_text(title_match.group("title")) if title_match else ""
        return price, title, href

    return None, "", ""

def refresh_row(row: dict[str, str], checked_at: str, timeout: int) -> tuple[dict[str, str], dict[str, str]]:
    sku = row.get("Internal Reference", "").strip()
    source_name = row.get("Source", "").strip()
    price_url = row.get("Price URL", "").strip()
    search_url = row.get("Search URL", "").strip()
    previous = parse_price_float(row.get("Last Observed Price"))
    current: float | None = None
    status = "OK"
    title = row.get("Title", "")
    notes = row.get("Notes", "")

    try:
        fetch_url = search_url if source_name in {"Farming Parts", "Lowe & Young"} and search_url else price_url
        source = fetch(fetch_url, timeout=timeout)
        if source_name == "Farming Parts":
            current, parsed_title, parsed_url = parse_shopify_meta_prices(source, sku)
            if parsed_title:
                title = parsed_title
            if parsed_url:
                price_url = parsed_url
        elif source_name == "Lowe & Young":
            current, parsed_title, parsed_url = parse_lowe_young_listing_for_sku(source, sku)
            if parsed_title:
                title = parsed_title
            if parsed_url:
                price_url = parsed_url
        else:
            status = "Unsupported Source"
    except Exception as exc:
        status = "Fetch Error"
        notes = f"{notes} Refresh error: {exc}".strip()

    if status == "OK" and current is None:
        status = "Price Not Found"

    changed = ""
    percent = ""
    recommendation = "No change"
    if previous is not None and current is not None:
        changed = "Yes" if round(previous, 2) != round(current, 2) else "No"
        if previous:
            percent = f"{((current - previous) / previous) * 100:.2f}"
        if changed == "Yes":
            recommendation = "Review price change before updating Odoo"
    elif current is not None:
        changed = "New"
        recommendation = "Review new source price"
    elif status != "OK":
        changed = "Unknown"
        recommendation = "Review source URL"

    updated = dict(row)
    if current is not None:
        updated["Last Observed Price"] = f"{current:.2f}"
    updated["Last Checked"] = checked_at
    updated["Title"] = title
    updated["Notes"] = notes

    delta = {
        "Internal Reference": sku,
        "Source": source_name,
        "Price URL": price_url,
        "Previous Price": "" if previous is None else f"{previous:.2f}",
        "Current Price": "" if current is None else f"{current:.2f}",
        "Currency": row.get("Currency", ""),
        "Changed": changed,
        "Percent Change": percent,
        "Status": status,
        "Confidence": row.get("Confidence", ""),
        "Checked At": checked_at,
        "Recommendation": recommendation,
        "Notes": notes,
    }
    return updated, delta


def read_registry(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Refresh saved retail price source URLs and emit a price delta report.")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.5)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--update-registry", action="store_true")
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()

    rows = [row for row in read_registry(args.registry) if (row.get("Active") or "Yes").lower() == "yes"]
    if args.limit:
        rows = rows[: args.limit]

    checked_at = datetime.now().isoformat(timespec="seconds")
    refreshed_rows = []
    delta_rows = []
    for index, row in enumerate(rows, start=1):
        updated, delta = refresh_row(row, checked_at, args.timeout)
        refreshed_rows.append(updated)
        delta_rows.append(delta)
        print(f"{index}/{len(rows)} {delta['Internal Reference']} {delta['Source']}: {delta['Status']} {delta['Previous Price']} -> {delta['Current Price']}")
        time.sleep(args.delay)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or f"retail_price_refresh_{stamp}"
    delta_path = PRICING_DIR / f"{run_name}_delta.csv"
    write_csv(delta_path, DELTA_FIELDS, delta_rows)

    if args.update_registry:
        existing = read_registry(args.registry)
        update_map = {(row["Internal Reference"], row["Source"], row["Price URL"]): row for row in refreshed_rows}
        merged = []
        for row in existing:
            key = (row.get("Internal Reference", ""), row.get("Source", ""), row.get("Price URL", ""))
            merged.append(update_map.get(key, row))
        write_csv(args.registry, REGISTRY_FIELDS, merged)

    print(f"Wrote {delta_path}")
    print(f"Checked: {len(delta_rows)}")
    print(f"Changed: {sum(1 for row in delta_rows if row['Changed'] == 'Yes')}")
    print(f"Errors: {sum(1 for row in delta_rows if row['Status'] != 'OK')}")
    if args.update_registry:
        print(f"Updated registry: {args.registry}")


if __name__ == "__main__":
    main()



