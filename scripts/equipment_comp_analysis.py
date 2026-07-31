"""Analyze equipment opportunities against authorized comparable-price data.

The engine compares seller ask directly with matched comp prices. It intentionally
does not estimate freight, repairs, inspection, tax, or financing. Odoo writes are
disabled unless --apply is supplied.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import re
import statistics
import xmlrpc.client
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
DEFAULT_LISTINGS = (
    ROOT
    / "outputs"
    / "facebook_marketplace_deals"
    / "facebook-sourcing-hardened-20260726.csv"
)
DEFAULT_OUTPUT = ROOT / "outputs" / "equipment_comp_analysis"
MINIMUM_COMP_COUNT = 3
GOOD_MAX_MEDIAN_MULTIPLIER = 1.10
MAX_COMP_HOURS_DIFFERENCE = 1000.0
MAX_COMP_YEAR_DIFFERENCE = 3

EQUIPMENT_CLASS_ALIASES = {
    "bulldozer": "dozer",
    "crawlerdozer": "dozer",
    "compactexcavator": "mini_excavator",
    "miniexcavator": "mini_excavator",
    "crawlerexcavator": "excavator",
    "trackedexcavator": "excavator",
    "compacttrackloader": "skid_steer",
    "trackloader": "skid_steer",
    "skidsteer": "skid_steer",
    "roughterrainforklift": "telehandler",
}

# Cross-brand comparisons are deliberately allowlisted. A model must appear in
# one of these peer groups; there is no type-only or numeric-name fallback.
CROSS_BRAND_MODEL_TIERS = {
    "mini_excavator": {
        "mini_2_3t": {
            "3017", "302", "3027", "17g", "17p", "26g", "26p", "e20",
            "e26", "u17", "u25", "kx018", "kx030", "vio17", "vio25",
            "tb216", "tb225", "e17c",
        },
        "mini_3_4t": {
            "3035", "30g", "35g", "35p", "e32", "e35", "e35i", "u35",
            "kx033", "kx040", "vio35", "vio38", "tb235", "tb240",
        },
        "mini_5_6t": {
            "304", "304c", "304e2", "305", "3055", "3055e2", "50g", "60g",
            "60p", "e50", "e55", "u55", "kx057", "kx1635", "vio50",
            "vio55", "tb250", "tb260", "pc55",
        },
        "mini_8_10t": {
            "3075", "308", "308e2", "75g", "75d", "85g", "85p", "e85",
            "kx080", "vio80", "sv100", "sv1002a", "tb290",
        },
    },
    "excavator": {
        "excavator_13_17t": {
            "313", "313gc", "314", "314dlcr", "315", "315gc", "316",
            "316el", "130g", "135g", "160d", "160g", "pc138", "pc138uslc8",
            "pc160", "sk140", "ec140", "ec160",
        },
        "excavator_20_27t": {
            "320", "320b", "323", "323f", "323fl", "324", "324el",
            "210g", "210glc", "240d", "240dlc", "245g", "pc210", "pc238",
            "pc240", "sk210", "sk260", "ec220", "ec250",
        },
        "excavator_30_40t": {
            "328dl", "330", "330fl", "335f", "335fl", "336", "336e",
            "336el", "336gc", "300g", "350g", "350glc", "350ptier",
            "380g", "pc360", "pc360lc10", "pc390", "ec350", "ec380",
        },
        "excavator_45_55t": {
            "349", "352", "450clc", "470g", "490g", "pc490",
            "pc490lc11", "ec480",
        },
    },
    "skid_steer": {
        "loader_small": {
            "239d", "249d", "257d", "259d", "259d3", "317g", "s66",
            "t450", "t550", "t590", "svl65", "svl652", "tl6",
        },
        "loader_medium": {
            "279d", "279d3", "289d", "289d3", "323g", "325g", "t64",
            "t66", "t662", "t650", "t740", "t76", "t770", "svl75",
            "svl752", "tl8",
        },
        "loader_large": {
            "299d", "299d2", "299d3", "331g", "333g", "t870", "svl90",
            "svl95", "svl952s", "svl97", "svl972", "tl10", "tl12",
            "tl12v2",
        },
    },
    "dozer": {
        "dozer_small": {
            "d1", "d3", "d3k2", "d4", "450", "450j", "450k", "550h",
            "550k", "d37", "d37px", "d39", "d39px", "650l",
        },
        "dozer_medium": {
            "d5", "d5k", "d5k2", "d6k", "d6k2", "d6n", "650j", "650k",
            "700k", "d51", "d51px", "d51px24", "d51pxi24", "d61",
            "d61px", "750j", "850m",
        },
        "dozer_large": {
            "d6r", "d6t", "d7", "750k", "850j", "850l", "d65", "d71",
            "d85", "1150", "1650",
        },
        "dozer_xlarge": {
            "d8", "d8t", "d9", "d10", "950k", "1050k", "d155",
            "d155ax6",
        },
    },
    "telehandler": {
        "telehandler_compact": {
            "g518", "g518a", "g519", "g519a", "gth5519", "50520",
            "50520tc", "52520", "ft5719",
        },
        "telehandler_6_7k": {
            "6042", "tl642", "tl642c", "tl642d", "gth644", "50742",
        },
        "telehandler_8_9k": {
            "8042", "943", "tl943", "gth844",
        },
        "telehandler_10k": {
            "g1055", "g1055a", "gth1056", "10054", "tl1055", "51056",
        },
    },
}


@dataclass(frozen=True)
class Comp:
    source: str
    equipment_type: str
    manufacturer: str
    model: str
    year: int | None
    hours: float | None
    price: float
    sale_type: str
    sale_date: date | None
    source_url: str


def normalized(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def equipment_class(value: object) -> str:
    normalized_value = normalized(value)
    return EQUIPMENT_CLASS_ALIASES.get(normalized_value, normalized_value)


def manufacturer_family(value: object) -> str:
    normalized_value = normalized(value)
    return {
        "cat": "caterpillar",
        "deere": "johndeere",
        "jd": "johndeere",
    }.get(normalized_value, normalized_value)


def model_family(manufacturer: object, model: object) -> str:
    family = normalized(model)
    if normalized(manufacturer) in {"cat", "caterpillar"}:
        family = re.sub(r"cr$", "", family)
    return family


def cross_brand_size_tier(equipment_type: object, model: object) -> str:
    model_key = normalized(model)
    model_keys = {
        model_key,
        re.sub(r"(cr|lgp|xl|lt|wlt|wt|xhp|xps)$", "", model_key),
    }
    for tier, models_in_tier in CROSS_BRAND_MODEL_TIERS.get(
        equipment_class(equipment_type), {}
    ).items():
        if model_keys & models_in_tier:
            return tier
    return ""


def text(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, "", False):
            return str(value).strip()
    return ""


def number(value: object) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", str(value or ""))
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def integer(value: object) -> int | None:
    parsed = int(number(value))
    return parsed or None


def parse_date(value: object) -> date | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None


def load_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def comp_from_row(row: dict) -> Comp | None:
    source = text(row, "source_name", "source", "Source") or "Authorized comp"
    if normalized(source) in {"machinerytrader", "machinerytradercom"}:
        rights_confirmed = text(
            row,
            "data_rights_confirmed",
            "Data Rights Confirmed",
        ).lower()
        authorization_reference = text(
            row,
            "authorization_reference",
            "Authorization Reference",
        )
        if rights_confirmed not in {"1", "true", "yes", "y"}:
            return None
        if not authorization_reference:
            return None
    status = text(row, "result_status", "Result Status").lower()
    sale_type = text(row, "sale_type", "Sale Type").lower()
    if status:
        if status != "sold":
            return None
        price_basis = text(row, "price_basis", "Price Basis").lower()
        if price_basis in ("", "unknown"):
            return None
        price = number(
            text(
                row,
                "total_price",
                "sold_price",
                "hammer_price",
                "Total Price",
                "Sold Price",
                "Hammer Price",
            )
        )
        sale_type = "auction_result"
    else:
        price = number(text(row, "price", "Price"))
        if sale_type not in {
            "auction_result",
            "retail_value",
            "wholesale_value",
            "asking",
            "manual",
        }:
            return None
    if price <= 0:
        return None
    currency = text(row, "currency", "Currency")
    if currency and currency.upper() != "USD":
        return None
    return Comp(
        source=source,
        equipment_type=text(row, "category", "equipment_type", "Equipment Type"),
        manufacturer=text(row, "make", "manufacturer", "Manufacturer"),
        model=text(row, "model", "Model"),
        year=integer(text(row, "year", "Year")),
        hours=number(text(row, "hours", "Hours")) or None,
        price=price,
        sale_type=sale_type,
        sale_date=parse_date(text(row, "sale_date", "Sale Date")),
        source_url=text(row, "canonical_url", "source_url", "Source URL"),
    )


def load_env() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def connect_odoo():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(
        db, username, api_key, {}
    )
    if not uid:
        raise RuntimeError("Odoo authentication failed.")
    return (
        db,
        uid,
        api_key,
        xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object"),
    )


def odoo_call(connection, model: str, method: str, args: list, kwargs=None):
    db, uid, api_key, models = connection
    return models.execute_kw(
        db, uid, api_key, model, method, args, kwargs or {}
    )


def odoo_search_read_all(
    connection,
    model: str,
    domain: list,
    fields: list[str],
    batch_size: int = 1000,
) -> list[dict]:
    """Read every matching record without relying on Odoo's search_read limit."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    record_ids = odoo_call(
        connection,
        model,
        "search",
        [domain],
        {"order": "id"},
    )
    rows = []
    for start in range(0, len(record_ids), batch_size):
        rows.extend(
            odoo_call(
                connection,
                model,
                "read",
                [record_ids[start : start + batch_size]],
                {"fields": fields},
            )
        )
    return rows


def odoo_comps(connection, company_id: int | None = None) -> list[Comp]:
    domain = [("company_id", "=", company_id)] if company_id else []
    rows = odoo_search_read_all(
        connection,
        "southern.equipment.comp",
        domain,
        [
            "source",
            "source_url",
            "equipment_type",
            "manufacturer",
            "model",
            "year",
            "hours",
            "price",
            "sale_type",
            "sale_date",
        ],
    )
    return [comp for row in rows if (comp := comp_from_row(row))]


def similarity(listing: dict, comp: Comp) -> tuple[float, str]:
    listing_make = manufacturer_family(text(listing, "Manufacturer"))
    listing_model = normalized(text(listing, "Model"))
    listing_type = equipment_class(text(listing, "Equipment Type"))
    if not listing_make or not listing_model:
        return 0.0, "missing listing manufacturer/model"
    same_make = manufacturer_family(comp.manufacturer) == listing_make
    comp_model = normalized(comp.model)
    if same_make and listing_model and comp_model == listing_model:
        base, level = 80.0, "exact model"
    elif (
        same_make
        and
        listing_model
        and model_family(listing_make, listing_model)
        and model_family(listing_make, listing_model)
        == model_family(comp.manufacturer, comp.model)
    ):
        base, level = 75.0, "compatible model family"
    else:
        # Cross-brand matching is authoritative only in Odoo v4, where both
        # records can be tied to documented specification profiles. The
        # external validator deliberately refuses model-name tier guesses.
        return 0.0, "model mismatch or cross-brand specs unavailable"
    listing_year = integer(text(listing, "Year"))
    if listing_year and comp.year:
        base += max(0.0, 12.0 - abs(listing_year - comp.year) * 3.0)
    listing_hours = number(text(listing, "Hours"))
    if listing_hours and comp.hours:
        base += max(0.0, 8.0 - abs(listing_hours - comp.hours) / 500.0)
    return min(base, 100.0), level


def age_hours_fit(listing: dict, comp: Comp) -> tuple[float, bool]:
    """Return a fit factor and whether the comp is close enough to prefer.

    Identity still comes first, but equipment values move materially with model
    year and machine hours. This factor keeps stale/high-hour or unusually
    low-hour comps from overpowering better-matched evidence.
    """
    factors = []
    preferred = []
    listing_year = integer(text(listing, "Year"))
    if listing_year and comp.year:
        diff = abs(listing_year - comp.year)
        if diff <= 1:
            factors.append(1.0)
        elif diff <= 3:
            factors.append(0.85)
        elif diff <= 5:
            factors.append(0.65)
        else:
            factors.append(0.40)
        preferred.append(diff <= 3)
    listing_hours = number(text(listing, "Hours"))
    if listing_hours and comp.hours:
        diff = abs(listing_hours - comp.hours)
        if diff <= 500:
            factors.append(1.0)
        elif diff <= 1500:
            factors.append(0.85)
        elif diff <= 3000:
            factors.append(0.65)
        else:
            factors.append(0.40)
        preferred.append(diff <= 2000)
    if not factors:
        return 1.0, True
    return math.prod(factors), all(preferred)


def comp_weight(comp: Comp, score: float, fit_factor: float = 1.0) -> float:
    type_weight = {
        "auction_result": 1.0,
        "wholesale_value": 0.9,
        "retail_value": 0.8,
        "manual": 0.75,
        "asking": 0.6,
    }.get(comp.sale_type, 0.0)
    if not type_weight:
        return 0.0
    age_weight = 1.0
    if comp.sale_date:
        days = max((date.today() - comp.sale_date).days, 0)
        age_weight = 0.5 ** (days / 730.0)
    return type_weight * age_weight * (score / 100.0) ** 2 * fit_factor


def weighted_quantile(values: list[tuple[float, float]], quantile: float) -> float:
    ordered = sorted((price, weight) for price, weight in values if weight > 0)
    total = sum(weight for _price, weight in ordered)
    if not ordered or total <= 0:
        return 0.0
    target = total * quantile
    running = 0.0
    for price, weight in ordered:
        running += weight
        if running >= target:
            return price
    return ordered[-1][0]


def filter_price_outliers(rows: list[tuple]) -> list[tuple]:
    """Remove extreme prices with a robust MAD rule, preserving small samples."""
    if len(rows) < 5:
        return rows
    prices = [row[0].price for row in rows]
    median = statistics.median(prices)
    mad = statistics.median(abs(price - median) for price in prices)
    if mad <= 0:
        return rows
    threshold = 3.5 * 1.4826 * mad
    filtered = [row for row in rows if abs(row[0].price - median) <= threshold]
    return filtered or rows


def confidence(
    matched: list[tuple[Comp, float, float]], cross_brand: bool = False
) -> str:
    exact = sum(normalized(comp.model) and score >= 80 for comp, score, _weight in matched)
    identity = sum(normalized(comp.model) and score >= 70 for comp, score, _weight in matched)
    sold = sum(comp.sale_type == "auction_result" for comp, _score, _weight in matched)
    if not cross_brand and len(matched) >= 6 and exact >= 4 and sold >= 3:
        return "high"
    if cross_brand and len(matched) >= MINIMUM_COMP_COUNT:
        return "medium"
    if len(matched) >= MINIMUM_COMP_COUNT and identity >= MINIMUM_COMP_COUNT:
        return "medium"
    return "low"


def score_deal(ask: float, low: float, median: float, high: float, conf: str):
    if not ask or not median or not high:
        return 0.0, "verify", 0.0
    discount = (median - ask) / median
    if ask <= low:
        raw_score = 85.0 + min(max((low - ask) / max(low, 1), 0.0), 0.3) * 50
    elif ask <= median:
        span = max(median - low, 1.0)
        raw_score = 70.0 + 15.0 * (median - ask) / span
    elif ask <= high:
        span = max(high - median, 1.0)
        raw_score = 45.0 + 25.0 * (high - ask) / span
    else:
        raw_score = max(0.0, 45.0 - 90.0 * (ask - high) / max(high, 1.0))
    factor = {"low": 0.65, "medium": 0.85, "high": 1.0}[conf]
    final = round(min(max(raw_score * factor, 0.0), 100.0), 1)
    if conf == "low":
        grade = "verify"
    elif ask <= low and discount >= 0.15:
        grade = "strong"
    elif ask <= median * GOOD_MAX_MEDIAN_MULTIPLIER:
        grade = "good"
    elif ask <= high:
        grade = "verify"
    else:
        grade = "pass"
    return final, grade, discount


def public_valuation_summary(
    ask: float,
    low: float,
    median: float,
    high: float,
    count: int,
    basis: str,
    conf: str,
    unavailable_reason: str = "",
    expanded_hours: bool = False,
) -> str:
    if not count or not median:
        if unavailable_reason == "missing_hours":
            return (
                "Comparable valuation is pending because machine hours were not "
                "provided. The listing remains available at the seller's asking price."
            )
        if unavailable_reason == "missing_year":
            return (
                "Comparable valuation is pending because model year was not provided. "
                "The listing remains available at the seller's asking price."
            )
        if unavailable_reason == "missing_identity":
            return (
                "Comparable valuation is pending until manufacturer, model, and "
                "equipment class are confirmed. The seller's asking price is shown."
            )
        return (
            "Comparable valuation is not available because there is not enough "
            "closely matched market data. The displayed price is the seller's ask."
        )
    basis_label = {
        "exact_model": "exact-model",
        "same_make_family": "same-manufacturer compatible-model",
        "cross_brand_peer": "cross-brand specification peer",
    }[basis]
    comparison = ""
    if ask:
        difference = abs((ask - median) / median) * 100.0
        position = "below" if ask < median else "above" if ask > median else "at"
        comparison = (
            f" Seller ask is {difference:.1f}% {position} the matched median."
            if position != "at"
            else " Seller ask is at the matched median."
        )
    hour_note = (
        " The comparison window was expanded from 500 to 1,000 hours."
        if expanded_hours
        else ""
    )
    return (
        f"Market comparison: {count} {basis_label} comp(s), "
        f"${low:,.0f}-${high:,.0f} range, ${median:,.0f} median, "
        f"{conf} confidence.{comparison}{hour_note} "
        "This is a comparison, not an appraisal."
    )


def analyze(listing: dict, comps: list[Comp]) -> dict[str, object]:
    candidates = []
    listing_type = equipment_class(text(listing, "Equipment Type"))
    listing_hours = number(text(listing, "Hours"))
    listing_year = integer(text(listing, "Year"))
    listing_make = manufacturer_family(text(listing, "Manufacturer"))
    listing_model = normalized(text(listing, "Model"))
    unavailable_reason = (
        "missing_identity"
        if not listing_type or not listing_make or not listing_model
        else "missing_year"
        if not listing_year
        else "missing_hours"
        if not listing_hours
        else ""
    )
    seen_sources = set()
    for comp in comps if not unavailable_reason else []:
        if listing_type and equipment_class(comp.equipment_type) != listing_type:
            continue
        if listing_year and (
            comp.year is None
            or abs(listing_year - comp.year) > MAX_COMP_YEAR_DIFFERENCE
        ):
            continue
        if listing_hours and (
            comp.hours is None
            or abs(listing_hours - comp.hours) > MAX_COMP_HOURS_DIFFERENCE
        ):
            continue
        sim, match_level = similarity(listing, comp)
        if sim < 50:
            continue
        fit_factor, preferred_year_hours = age_hours_fit(listing, comp)
        weight = comp_weight(comp, sim, fit_factor)
        if weight:
            source_key = comp.source_url.strip().lower()
            dedupe_key = (
                ("url", source_key)
                if source_key
                else (
                    "facts",
                    normalized(comp.source),
                    equipment_class(comp.equipment_type),
                    manufacturer_family(comp.manufacturer),
                    normalized(comp.model),
                    comp.year,
                    comp.hours,
                    comp.price,
                    comp.sale_date,
                )
            )
            if dedupe_key in seen_sources:
                continue
            seen_sources.add(dedupe_key)
            candidates.append((comp, sim, weight, match_level, preferred_year_hours))
    primary = [
        row
        for row in candidates
        if row[3] in {"exact model", "compatible model family"}
    ]
    cross_brand = [row for row in candidates if row[3] == "cross-brand size tier"]
    close_primary = filter_price_outliers(
        [
            row
            for row in primary
            if abs(listing_hours - (row[0].hours or 0.0)) <= 500.0
        ]
    )
    primary = (
        close_primary
        if len(close_primary) >= MINIMUM_COMP_COUNT
        else filter_price_outliers(primary)
    )
    if len(primary) >= MINIMUM_COMP_COUNT:
        selected = primary
        basis = (
            "exact_model"
            if all(row[3] == "exact model" for row in selected)
            else "same_make_family"
        )
    else:
        close_cross_brand = filter_price_outliers(
            [
                row
                for row in cross_brand
                if abs(listing_hours - (row[0].hours or 0.0)) <= 500.0
            ]
        )
        cross_brand = (
            close_cross_brand
            if len(close_cross_brand) >= MINIMUM_COMP_COUNT
            else filter_price_outliers(cross_brand)
        )
        if len(cross_brand) >= MINIMUM_COMP_COUNT:
            selected = cross_brand
            basis = "cross_brand_peer"
        else:
            selected = []
            basis = "insufficient"
    weighted = [(comp.price, weight) for comp, _sim, weight, _level, _close in selected]
    low = weighted_quantile(weighted, 0.25)
    median = weighted_quantile(weighted, 0.50)
    high = weighted_quantile(weighted, 0.75)
    matched = [(comp, sim, weight) for comp, sim, weight, _level, _close in selected]
    expanded_hours = any(
        abs(listing_hours - (comp.hours or 0.0)) > 500.0
        for comp, _sim, _weight, _level, _close in selected
    )
    conf = confidence(matched, cross_brand=basis == "cross_brand_peer")
    if expanded_hours and conf == "high":
        conf = "medium"
    ask = number(text(listing, "Seller Ask", "Seller Ask Price", "Ask Price"))
    deal_score, grade, discount = score_deal(ask, low, median, high, conf)
    return {
        "Source Listing ID": text(listing, "Source Listing ID", "Equipment ID"),
        "Public Title": text(
            listing, "Standardized Title", "Public Title", "Equipment Name"
        ),
        "Seller Ask": f"{ask:.2f}",
        "Comp Low": f"{low:.2f}",
        "Comp Median": f"{median:.2f}",
        "Comp High": f"{high:.2f}",
        "Comp Count": len(selected),
        "Comp Confidence": conf,
        "Comp Match Basis": basis,
        "Discount to Median %": f"{discount * 100:.1f}" if median else "",
        "Deal Score": f"{deal_score:.1f}",
        "Grade": grade,
        "Public Valuation Summary": public_valuation_summary(
            ask,
            low,
            median,
            high,
            len(selected),
            basis,
            conf,
            unavailable_reason,
            expanded_hours,
        ),
        "Recommendation": (
            "Insufficient comparable evidence"
            if len(selected) < MINIMUM_COMP_COUNT
            else f"{grade.title()} based only on seller ask versus matched comp range"
        ),
        "Method": (
            "At least three exact/same-manufacturer compatible models are preferred. "
            "When that pool is insufficient, at least three allowlisted cross-brand "
            "size-tier peers are required and are never mixed with primary comps. "
            "Weighted by realized/asking type, recency, identity similarity, "
            "model-year proximity, and hour proximity; equipment class must match, "
            "listing year and hours are required for a numeric range, source records "
            "are deduplicated, robust price outliers are removed, and comps must be "
            "within three years; the hour window starts at 500 and expands to "
            "1,000 only when needed to obtain three comps. "
            "No freight, repair, inspection, tax, or financing assumptions."
        ),
    }


def write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listings", type=Path, default=DEFAULT_LISTINGS)
    parser.add_argument("--comps", type=Path, action="append", default=[])
    parser.add_argument("--include-odoo-comps", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    listings = load_csv(args.listings)
    comps: list[Comp] = []
    for path in args.comps:
        comps.extend(comp for row in load_csv(path) if (comp := comp_from_row(row)))
    connection = None
    if args.include_odoo_comps or args.apply:
        connection = connect_odoo()
    if args.include_odoo_comps:
        comps.extend(odoo_comps(connection))
    results = [analyze(listing, comps) for listing in listings]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output_dir / f"equipment-comp-analysis-{stamp}.csv"
    write_csv(output, results)
    eligible = [row for row in results if int(row["Comp Count"]) >= MINIMUM_COMP_COUNT]
    if args.apply:
        if connection is None:
            raise RuntimeError("Odoo connection unavailable.")
        for row in eligible:
            ids = odoo_call(
                connection,
                "southern.equipment.listing",
                "search",
                [[
                    ("source", "=", "facebook_marketplace"),
                    ("source_listing_id", "=", row["Source Listing ID"]),
                ]],
                {"limit": 2},
            )
            if len(ids) != 1:
                raise RuntimeError(
                    f"{row['Source Listing ID']}: expected exactly one Odoo match."
                )
            odoo_call(
                connection,
                "southern.equipment.listing",
                "write",
                [ids, {
                    "comp_low": number(row["Comp Low"]),
                    "comp_median": number(row["Comp Median"]),
                    "comp_high": number(row["Comp High"]),
                    "comp_count": int(row["Comp Count"]),
                    "comp_confidence": row["Comp Confidence"],
                    "estimated_market_value": number(row["Comp Median"]),
                    "expected_resale": number(row["Comp Median"]),
                    "deal_score": number(row["Deal Score"]),
                    "grade": row["Grade"],
                    "public_deal_summary": (
                        f"Listed {abs(number(row['Discount to Median %'])):.1f}% "
                        f"{'below' if number(row['Discount to Median %']) >= 0 else 'above'} "
                        "the matched comparable median."
                    ),
                }],
            )
    print(f"LISTINGS={len(listings)}")
    print(f"COMPS={len(comps)}")
    print(f"ELIGIBLE={len(eligible)}")
    print(f"APPLIED={len(eligible) if args.apply else 0}")
    print(f"OUTPUT={output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
