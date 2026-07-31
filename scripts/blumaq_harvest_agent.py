from __future__ import annotations

import argparse
import csv
import html
import json
import re
import time
import urllib.parse
import urllib.request
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT_DIR = ROOT / "odoo_imports" / "product_master" / "blumaq"
BASE_URL = "https://www.blumaq.com"
DEFAULT_SEEDS = [
    "https://www.blumaq.com/en/spare-parts/blumaq/BQ101636414-filter-suitable-1383100bq/",
    "https://www.blumaq.com/en/spare-parts/blumaq/BQ103122555-filter-suitable-1561200bq/",
    "https://www.blumaq.com/en/spare-parts/blumaq/BQ101632160-filter-suitable-1799806bq/",
    "https://www.blumaq.com/en/spare-parts/blumaq/BQ103352895-hydraulic-element/",
    "https://www.blumaq.com/en/spare-parts/blumaq/BQHD003-distribution-sheet/",
    "https://www.blumaq.com/en/spare-parts/caterpillar/3652574-pin/",
    "https://www.blumaq.com/en/spare-parts/caterpillar/8W2842-cap-assy/",
    "https://www.blumaq.com/en/spare-parts/caterpillar/1U3517-cover/",
]


def clean_text(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", value or "", flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", clean_text(value).lower()).strip("_")


def absolute_url(value: str, base: str) -> str:
    return urllib.parse.urljoin(base, html.unescape(value or "")).split("#", 1)[0]


def fetch(url: str, timeout: int) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Southern Equipment catalog sourcing bot; contact via titan-equip.com",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="replace")


def extract_first(pattern: str, text: str, default: str = "", flags: int = re.I | re.S) -> str:
    match = re.search(pattern, text, flags)
    return clean_text(match.group(1)) if match else default


def extract_reference(page_text: str, title: str, url: str) -> str:
    for match in re.finditer(r"Reference\s*:?\s*>?\s*([A-Z0-9][A-Z0-9._-]{2,})", page_text, flags=re.I):
        candidate = match.group(1).strip().strip(".")
        if not candidate.lower().startswith(("http", "www")):
            return candidate.upper()
    title_match = re.search(r"'([^']+)'\s*[^()]*\(([^)]+)\)", title)
    if title_match:
        return title_match.group(2).strip().upper()
    path_match = re.search(r"/spare-parts/[^/]+/([^/-]+)-", url, flags=re.I)
    return path_match.group(1).upper() if path_match else ""


def extract_name(title: str, plain: str, reference: str) -> str:
    title_match = re.search(r"'[^']+'\s*([^()»]+)", title)
    if title_match:
        name = clean_text(title_match.group(1))
    else:
        name = extract_first(r"<h1[^>]*>(.*?)</h1>", plain) or title.split("»", 1)[0]
    name = re.sub(rf"\b{re.escape(reference)}\b", "", name, flags=re.I).strip(" '-")
    return clean_text(name).upper() if name else reference


def extract_category(plain_text: str, url: str, name: str) -> str:
    text = plain_text.upper()
    if "HYDRAULIC" in text or "HYDRAULIC" in name.upper():
        return "Parts / Hydraulic"
    if "FILTERS" in text or "FILTER" in name.upper():
        if "AIR FILTER" in text or "AIR FILTER" in name.upper():
            return "Parts / Filters / Air Filters"
        if "FUEL" in text or "FUEL" in name.upper():
            return "Parts / Filters / Fuel Filters"
        if "HYDRAULIC OIL FILTER" in text:
            return "Parts / Filters / Hydraulic Filters"
        if "ENGINE OIL" in text:
            return "Parts / Filters / Engine Oil Filters"
        return "Parts / Filters"
    if "GASKET" in name.upper() or "SEAL" in name.upper():
        return "Parts / Seals"
    if "BOLT" in name.upper() or "NUT" in name.upper() or "WASHER" in name.upper():
        return "Parts / Hardware"
    if "CAP ASSY" in name.upper() or "PIN" in name.upper() or "COVER" in name.upper():
        return "Parts / Heavy Equipment"
    if "/caterpillar/" in url.lower():
        return "Parts / Heavy Equipment / Caterpillar"
    return "Parts / Miscellaneous"


def extract_images(source: str, base_url: str) -> list[str]:
    images: list[str] = []
    product_schema = extract_product_schema(source)
    schema_image = product_schema.get("image") if product_schema else ""
    if isinstance(schema_image, str) and schema_image:
        images.append(absolute_url(schema_image, base_url))
    elif isinstance(schema_image, list):
        images.extend(absolute_url(item, base_url) for item in schema_image if isinstance(item, str))
    for match in re.finditer(r"background-image\s*:\s*url\(([^)]+)\)", source, flags=re.I):
        url = absolute_url(match.group(1).strip("'\" "), base_url)
        if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) or "images.blumaq.com" in url.lower():
            images.append(url)
    for match in re.finditer(r"<img\b[^>]+(?:src|data-cmplz-src)=['\"]([^'\"]+)['\"][^>]*>", source, flags=re.I):
        url = absolute_url(match.group(1), base_url)
        if any(token in url.lower() for token in ["logo", "whatsapp", "cookie", "mantenimiento", "asesoramiento", "logistica"]):
            continue
        if url.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) or "images.blumaq.com" in url.lower():
            images.append(url)
    return [url for url in dict.fromkeys(images) if not url.lower().endswith("/vip/no-image.jpg")]


def extract_product_schema(source: str) -> dict[str, Any]:
    for match in re.finditer(r"<script[^>]+type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>", source, flags=re.I | re.S):
        raw = html.unescape(match.group(1)).strip()
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            continue
        candidates = payload if isinstance(payload, list) else [payload]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") == "Product":
                return item
    return {}


def extract_product_links(source: str, base_url: str) -> list[str]:
    links: list[str] = []
    for match in re.finditer(r"<a\b[^>]+href=['\"]([^'\"]+)['\"]", source, flags=re.I):
        url = absolute_url(match.group(1), base_url)
        if re.search(r"/en/spare-parts/[^/]+/[A-Z0-9._-]+-[^/]+/$", url, flags=re.I):
            links.append(url)
    return list(dict.fromkeys(links))


def parse_product(source: str, url: str, harvested_at: str) -> dict[str, Any] | None:
    product_schema = extract_product_schema(source)
    title = extract_first(r"<title[^>]*>(.*?)</title>", source)
    plain = clean_text(source)
    reference = extract_reference(plain, title, url)
    if not reference:
        return None
    name = extract_name(title, source, reference)
    brand = "Blumaq"
    if "/caterpillar/" in url.lower() or "Suitable for Caterpillar" in plain:
        brand = "Suitable for Caterpillar"
    weight = extract_first(r"Weight\s*:?\s*([0-9.,]+)", plain)
    availability = extract_first(r"Availability\s*:?\s*([A-Za-z ]+)", plain)
    group = extract_first(r'<span class="entry-group">(.*?)</span>', source)
    family = extract_first(r'<span class="entry-family">(.*?)</span>', source)
    schema_brand = product_schema.get("brand", {}).get("name") if isinstance(product_schema.get("brand"), dict) else ""
    manufacturer_signal = schema_brand or brand
    category = extract_category(plain, url, name)
    images = extract_images(source, url)
    related = extract_product_links(source, url)
    related_parts = []
    for related_url in related:
        related_ref = extract_reference("", "", related_url)
        if related_ref:
            related_parts.append(
                {
                    "internal_reference": f"BLQ-{related_ref}",
                    "relationship_type": "related",
                    "source_name": "Blumaq",
                    "source_url": url,
                    "confidence": 0.75,
                }
            )
    oem_references = []
    suitable_match = re.search(r"\bSUITABLE\s+([A-Z0-9._-]{4,})\b", name, flags=re.I)
    if suitable_match and suitable_match.group(1).upper() != reference.upper():
        oem_references.append(
            {
                "manufacturer": "Unknown OEM",
                "oem_part_number": suitable_match.group(1).upper(),
                "reference_type": "alternate",
                "source_name": "Blumaq",
                "source_url": url,
                "confidence": 0.6,
            }
        )
    specifications = [
        {"group": "Product", "name": "Reference", "value": reference, "source_name": "Blumaq", "source_url": url},
        {"group": "Product", "name": "Supplier Signal", "value": brand, "source_name": "Blumaq", "source_url": url},
    ]
    if weight:
        specifications.append({"group": "Specifications", "name": "Weight", "value": weight, "source_name": "Blumaq", "source_url": url})
    if availability:
        specifications.append({"group": "Product", "name": "Availability", "value": availability, "source_name": "Blumaq", "source_url": url})
    if group:
        specifications.append({"group": "Product", "name": "Group", "value": group, "source_name": "Blumaq", "source_url": url})
    if family:
        specifications.append({"group": "Product", "name": "Family", "value": family, "source_name": "Blumaq", "source_url": url})
    description_lines = [
        f"Blumaq source: {url}",
        f"Reference: {reference}",
        f"Product: {name}",
        f"Supplier/manufacturer signal: {manufacturer_signal}",
    ]
    if weight:
        description_lines.append(f"Weight: {weight}")
    if availability:
        description_lines.append(f"Availability: {availability}")
    description_lines.append("Public Blumaq page harvested for sourcing/catalog expansion. Pricing requires separate review.")
    return {
        "source": {
            "vendor": "Blumaq",
            "url": url,
            "harvested_at": harvested_at,
            "harvest_mode": "public_product_page",
        },
        "product": {
            "internal_reference": f"BLQ-{reference}",
            "supplier_sku": reference,
            "name": name,
            "category": category,
            "manufacturer": "Blumaq",
            "vendor_code": reference,
            "vendor_price": 0.0,
            "lead_time_days": 3,
        },
        "supplier_signal": brand,
        "weight": weight,
        "availability": availability,
        "specifications": specifications,
        "oem_references": oem_references,
        "related_parts": related_parts,
        "catalog_pages": [],
        "fitments": [],
        "alternate_barcodes": [],
        "description": "\n".join(description_lines),
        "image_url": images[0] if images else "",
        "image_urls": images,
        "related_urls": related,
        "enrichment_status": "Public Blumaq detail harvested; pricing/cost pending review",
    }


def write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = [
        "Internal Reference",
        "Supplier SKU",
        "Name",
        "Supplier",
        "Manufacturer",
        "Product Category",
        "Source URL",
        "Image URL",
        "Weight",
        "Availability",
        "Description",
    ]
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "Internal Reference": record["product"]["internal_reference"],
                    "Supplier SKU": record["product"]["supplier_sku"],
                    "Name": record["product"]["name"],
                    "Supplier": "Blumaq",
                    "Manufacturer": record["product"]["manufacturer"],
                    "Product Category": record["product"]["category"],
                    "Source URL": record["source"]["url"],
                    "Image URL": record["image_url"],
                    "Weight": record["weight"],
                    "Availability": record["availability"],
                    "Description": record["description"],
                }
            )


def main() -> None:
    parser = argparse.ArgumentParser(description="Harvest public Blumaq product pages into Odoo-ready JSON skeletons.")
    parser.add_argument("--seed-url", action="append", default=[])
    parser.add_argument("--seed-file", type=Path)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    parser.add_argument("--run-name", default="blumaq_public_seed_test")
    parser.add_argument("--max-products", type=int, default=50)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--timeout", type=int, default=30)
    args = parser.parse_args()

    seeds = list(args.seed_url)
    if args.seed_file and args.seed_file.exists():
        seeds.extend(line.strip() for line in args.seed_file.read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#"))
    if not seeds:
        seeds = DEFAULT_SEEDS

    args.out_dir.mkdir(parents=True, exist_ok=True)
    harvested_at = datetime.now().isoformat(timespec="seconds")
    queue = deque(dict.fromkeys(seeds))
    seen_urls: set[str] = set()
    seen_refs: set[str] = set()
    records: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    while queue and len(records) < args.max_products:
        url = queue.popleft()
        if url in seen_urls:
            continue
        seen_urls.add(url)
        try:
            source = fetch(url, args.timeout)
            record = parse_product(source, url, harvested_at)
            if not record:
                errors.append({"url": url, "error": "No product reference found"})
            elif record["product"]["internal_reference"] not in seen_refs:
                records.append(record)
                seen_refs.add(record["product"]["internal_reference"])
                for related_url in record.get("related_urls", []):
                    if related_url not in seen_urls:
                        queue.append(related_url)
            time.sleep(max(0.0, args.delay))
        except Exception as exc:
            errors.append({"url": url, "error": str(exc)})

    run_slug = slug(args.run_name)
    json_path = args.out_dir / f"{run_slug}_skeleton.json"
    csv_path = args.out_dir / f"{run_slug}_skeleton.csv"
    summary_path = args.out_dir / f"{run_slug}_summary.json"
    json_path.write_text(json.dumps(records, indent=2), encoding="utf-8")
    write_csv(csv_path, records)
    summary_path.write_text(
        json.dumps(
            {
                "run_name": args.run_name,
                "harvested_at": harvested_at,
                "seed_count": len(seeds),
                "visited_urls": len(seen_urls),
                "records": len(records),
                "queued_remaining": len(queue),
                "errors": errors,
                "json_path": str(json_path),
                "csv_path": str(csv_path),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"JSON: {json_path}")
    print(f"CSV: {csv_path}")
    print(f"Summary: {summary_path}")
    print(f"Records: {len(records)}")
    print(f"Errors: {len(errors)}")


if __name__ == "__main__":
    main()
