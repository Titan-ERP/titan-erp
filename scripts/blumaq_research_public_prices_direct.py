from __future__ import annotations

import argparse
import csv
import html
import re
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "odoo_imports" / "product_master" / "blumaq" / "pricing"


SOURCE_PATTERNS = [
    ("GT Engine Parts", "https://gtengineparts.com/caterpillar/{code}"),
    ("Aftermarket Express", "https://aftermarket.express/caterpillar/{code}"),
    ("Aftermarket Supply", "https://aftermarket.supply/caterpillar/{code}"),
]


def clean_text(value: str) -> str:
    text = re.sub(r"<script\b.*?</script>", " ", value or "", flags=re.I | re.S)
    text = re.sub(r"<style\b.*?</style>", " ", text, flags=re.I | re.S)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def normalize_code(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def display_code(value: str) -> str:
    return normalize_code(value)


def candidate_codes(row: dict[str, str]) -> list[str]:
    values = [row.get("Supplier SKU", "")]
    name = row.get("Name", "")
    for match in re.finditer(r"\bSuitable\s+([A-Z0-9]+?)(?:BQ|Q|OR)?\b", name, flags=re.I):
        values.append(match.group(1))
    for match in re.finditer(r"\b([0-9][A-Z0-9]{4,})\b", name, flags=re.I):
        values.append(match.group(1))
    return list(dict.fromkeys(code for code in (display_code(value) for value in values) if len(code) >= 4))


def fetch(url: str, timeout: int = 25) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 Southern Equipment Blumaq retail price research",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read()
        charset = response.headers.get_content_charset() or "utf-8"
    return data.decode(charset, errors="replace")


def parse_prices(source: str) -> list[float]:
    prices: list[float] = []
    plain = clean_text(source)
    for pattern in [
        r"\$([0-9][0-9,]*\.\d{2})\s*ea\b",
        r"##\s*\$([0-9][0-9,]*\.\d{2})",
        r"Price:\s*\$([0-9][0-9,]*\.\d{2})",
    ]:
        for match in re.finditer(pattern, plain, flags=re.I):
            prices.append(float(match.group(1).replace(",", "")))
    return list(dict.fromkeys(prices))


def detect_condition(source: str) -> str:
    plain = clean_text(source).upper()
    if "CONDITION: USED" in plain:
        return "USED"
    if "CONDITION: NEW AFTERMARKET" in plain or "NEW AFTERMARKET" in plain:
        return "NEW AFTERMARKET"
    if "CONDITION: NEW" in plain:
        return "NEW"
    return "UNKNOWN"


def exact_match(source: str, code: str) -> bool:
    normalized_source = normalize_code(clean_text(source))
    return normalize_code(code) in normalized_source


def confidence_for(row: dict[str, str], code: str, source_name: str, condition: str) -> float:
    supplier = normalize_code(row.get("Supplier SKU", ""))
    if normalize_code(code) == supplier:
        base = 0.88
    else:
        base = 0.8
    if condition == "USED":
        base -= 0.18
    if source_name == "Aftermarket Supply":
        base -= 0.02
    return round(max(base, 0.55), 2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Directly research public USD prices for Blumaq BLQ products from repeatable source URL patterns.")
    parser.add_argument("queue_csv", type=Path)
    parser.add_argument("--limit", type=int, default=80)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--delay", type=float, default=0.25)
    parser.add_argument("--timeout", type=int, default=10)
    parser.add_argument("--source", action="append", default=[], help="Optional source-name filter, e.g. 'GT Engine Parts'.")
    parser.add_argument("--include-used", action="store_true")
    parser.add_argument("--run-name", default="")
    args = parser.parse_args()

    rows = list(csv.DictReader(args.queue_csv.open(encoding="utf-8-sig")))
    targets = [row for row in rows if float(row.get("Current Sales Price") or 0) == 1.0][args.offset : args.offset + args.limit]
    source_patterns = SOURCE_PATTERNS
    if args.source:
        selected = {name.lower() for name in args.source}
        source_patterns = [item for item in SOURCE_PATTERNS if item[0].lower() in selected]
    observations: list[dict[str, Any]] = []
    attempts = 0
    hits = 0

    for index, row in enumerate(targets, start=1):
        row_hits = 0
        for code in candidate_codes(row):
            for source_name, pattern in source_patterns:
                url = pattern.format(code=code.lower())
                attempts += 1
                try:
                    source = fetch(url, timeout=args.timeout)
                    if not exact_match(source, code):
                        continue
                    condition = detect_condition(source)
                    if condition == "USED" and not args.include_used:
                        continue
                    prices = parse_prices(source)
                    if not prices:
                        continue
                    for price in prices[:3]:
                        observations.append(
                            {
                                "Internal Reference": row["Internal Reference"],
                                "Supplier SKU": row["Supplier SKU"],
                                "Observed Retail Price": price,
                                "Currency": "USD",
                                "Source": source_name,
                                "Source URL": url,
                                "Confidence": confidence_for(row, code, source_name, condition),
                                "Notes": f"Direct exact-code public source match on {code}; condition={condition}; source code may be supplier SKU or OEM/reference from Blumaq product name.",
                            }
                        )
                    row_hits += len(prices)
                    hits += len(prices)
                except Exception:
                    pass
                time.sleep(max(0.0, args.delay))
        print(f"{index}/{len(targets)} {row['Internal Reference']}: {row_hits} price observations", flush=True)

    deduped = []
    seen = set()
    for row in observations:
        key = (row["Internal Reference"], row["Observed Retail Price"], row["Source"], row["Source URL"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_name = args.run_name or f"blumaq_direct_public_price_observations_{stamp}"
    out_path = OUT_DIR / f"{run_name}.csv"
    fieldnames = [
        "Internal Reference",
        "Supplier SKU",
        "Observed Retail Price",
        "Currency",
        "Source",
        "Source URL",
        "Confidence",
        "Notes",
    ]
    with out_path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(deduped)

    print(f"Wrote {out_path}")
    print(f"Targets: {len(targets)}")
    print(f"Attempts: {attempts}")
    print(f"Observations: {len(deduped)}")
    print(f"SKUs with observations: {len({row['Internal Reference'] for row in deduped})}")


if __name__ == "__main__":
    main()
