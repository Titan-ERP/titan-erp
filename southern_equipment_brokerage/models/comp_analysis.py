import re

from odoo import _, fields, models


BROKER_GROUPS = (
    "southern_equipment_brokerage.group_southern_equipment_admin,"
    "southern_equipment_brokerage.group_southern_deal_broker"
)
MAX_COMP_HOURS_DIFFERENCE = 500.0
MAX_COMP_YEAR_DIFFERENCE = 3
CROSS_BRAND_MINIMUM_COMP_COUNT = 3
GOOD_MAX_MEDIAN_MULTIPLIER = 1.10
METHOD_VERSION = (
    "native-v2-tiered-hours-required-cross-brand-3-comp-mad-outliers-"
    "500-hours-3-years-110pct"
)
TERMINAL_STATUSES = ("archived", "sold")

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


def _normalized(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _equipment_class(value):
    normalized = _normalized(value)
    return EQUIPMENT_CLASS_ALIASES.get(normalized, normalized)


def _manufacturer_family(value):
    normalized = _normalized(value)
    return {
        "cat": "caterpillar",
        "deere": "johndeere",
        "jd": "johndeere",
    }.get(normalized, normalized)


def _model_family(manufacturer, model):
    family = _normalized(model)
    if _normalized(manufacturer) in {"cat", "caterpillar"}:
        family = re.sub(r"cr$", "", family)
    return family


def _cross_brand_size_tier(equipment_type, model):
    model_key = _normalized(model)
    model_keys = {
        model_key,
        re.sub(r"(cr|lgp|xl|lt|wlt|wt|xhp|xps)$", "", model_key),
    }
    for tier, models_in_tier in CROSS_BRAND_MODEL_TIERS.get(
        _equipment_class(equipment_type), {}
    ).items():
        if model_keys & models_in_tier:
            return tier
    return ""


def _weighted_quantile(values, quantile):
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


def _filter_price_outliers(rows):
    """Remove extreme prices with a robust MAD rule, preserving small samples."""
    if len(rows) < 5:
        return rows
    prices = sorted(row[0].price for row in rows)
    midpoint = len(prices) // 2
    median = (
        prices[midpoint]
        if len(prices) % 2
        else (prices[midpoint - 1] + prices[midpoint]) / 2.0
    )
    deviations = sorted(abs(price - median) for price in prices)
    mad = (
        deviations[midpoint]
        if len(deviations) % 2
        else (deviations[midpoint - 1] + deviations[midpoint]) / 2.0
    )
    if mad <= 0:
        return rows
    threshold = 3.5 * 1.4826 * mad
    filtered = [row for row in rows if abs(row[0].price - median) <= threshold]
    return filtered or rows


def _score_deal(ask, low, median, high, confidence):
    if not ask or not median or not high:
        return 0.0, "verify"
    discount = (median - ask) / median
    if ask <= low:
        raw_score = 85.0 + min(max((low - ask) / max(low, 1), 0.0), 0.3) * 50
    elif ask <= median:
        raw_score = 70.0 + 15.0 * (median - ask) / max(median - low, 1.0)
    elif ask <= high:
        raw_score = 45.0 + 25.0 * (high - ask) / max(high - median, 1.0)
    else:
        raw_score = max(0.0, 45.0 - 90.0 * (ask - high) / max(high, 1.0))
    factor = {"low": 0.65, "medium": 0.85, "high": 1.0}[confidence]
    score = round(min(max(raw_score * factor, 0.0), 100.0), 1)
    if confidence == "low":
        grade = "verify"
    elif ask <= low and discount >= 0.15:
        grade = "strong"
    elif ask <= median * GOOD_MAX_MEDIAN_MULTIPLIER:
        grade = "good"
    elif ask <= high:
        grade = "verify"
    else:
        grade = "pass"
    return score, grade


def _public_valuation_summary(
    ask, low, median, high, count, basis, confidence, unavailable_reason=""
):
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
        "cross_brand_peer": "cross-brand size-tier peer",
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
    return (
        f"Market comparison: {count} {basis_label} comp(s), "
        f"${low:,.0f}-${high:,.0f} range, ${median:,.0f} median, "
        f"{confidence} confidence.{comparison} This is a comparison, not an appraisal."
    )


class SouthernEquipmentListingCompAnalysis(models.Model):
    _inherit = "southern.equipment.listing"

    comp_last_calculated_at = fields.Datetime(
        string="Comp Analysis Updated", readonly=True, groups=BROKER_GROUPS
    )
    comp_method_version = fields.Char(
        string="Comp Method", readonly=True, groups=BROKER_GROUPS
    )
    comp_match_basis = fields.Selection(
        [
            ("insufficient", "Insufficient Closely Matched Data"),
            ("exact_model", "Exact Model"),
            ("same_make_family", "Same-Manufacturer Compatible Model"),
            ("cross_brand_peer", "Cross-Brand Size-Tier Peers"),
        ],
        string="Comp Match Basis",
        default="insufficient",
        readonly=True,
        groups=BROKER_GROUPS,
    )

    def _compatible_comp_rows(self, comps):
        self.ensure_one()
        listing_make = _manufacturer_family(self.manufacturer)
        listing_model = _normalized(self.model)
        listing_type = _equipment_class(self.equipment_type)
        if (
            not listing_make
            or not listing_model
            or not listing_type
            or not self.year
            or not self.hours
        ):
            return []
        listing_family = _model_family(self.manufacturer, self.model)
        listing_tier = _cross_brand_size_tier(self.equipment_type, self.model)
        primary_rows = []
        cross_brand_rows = []
        seen_sources = set()
        today = fields.Date.context_today(self)
        for comp in comps:
            if comp.price <= 0:
                continue
            if _equipment_class(comp.equipment_type) != listing_type:
                continue
            same_make = _manufacturer_family(comp.manufacturer) == listing_make
            comp_model = _normalized(comp.model)
            exact = same_make and comp_model == listing_model
            family = (
                same_make
                and bool(listing_family)
                and listing_family == _model_family(comp.manufacturer, comp.model)
            )
            cross_brand = (
                not same_make
                and bool(listing_tier)
                and listing_tier
                == _cross_brand_size_tier(comp.equipment_type, comp.model)
            )
            if not exact and not family and not cross_brand:
                continue
            if self.year and (
                not comp.year
                or abs(self.year - comp.year) > MAX_COMP_YEAR_DIFFERENCE
            ):
                continue
            if self.hours and (
                not comp.hours
                or abs(self.hours - comp.hours) > MAX_COMP_HOURS_DIFFERENCE
            ):
                continue

            similarity = 80.0 if exact else 75.0 if family else 60.0
            fit_factor = 1.0
            if self.year and comp.year:
                difference = abs(self.year - comp.year)
                similarity += max(0.0, 12.0 - difference * 3.0)
                fit_factor *= 1.0 if difference <= 1 else 0.85
            if self.hours and comp.hours:
                similarity += max(
                    0.0, 8.0 - abs(self.hours - comp.hours) / 500.0
                )

            type_weight = {
                "auction_result": 1.0,
                "wholesale_value": 0.9,
                "vip_valuation": 0.9,
                "retail_value": 0.8,
                "manual": 0.75,
                "asking": 0.6,
            }.get(comp.sale_type, 0.0)
            if not type_weight:
                continue
            source_key = (comp.source_url or "").strip().lower()
            dedupe_key = ("url", source_key) if source_key else ("record", comp.id)
            if dedupe_key in seen_sources:
                continue
            seen_sources.add(dedupe_key)
            age_weight = 1.0
            if comp.sale_date:
                days = max((today - comp.sale_date).days, 0)
                age_weight = 0.5 ** (days / 730.0)
            weight = (
                type_weight
                * age_weight
                * (min(similarity, 100.0) / 100.0) ** 2
                * fit_factor
            )
            if weight > 0:
                row = (comp, min(similarity, 100.0), weight, exact, cross_brand)
                if cross_brand:
                    cross_brand_rows.append(row)
                else:
                    primary_rows.append(row)
        if primary_rows:
            return _filter_price_outliers(primary_rows)
        cross_brand_rows = _filter_price_outliers(cross_brand_rows)
        if len(cross_brand_rows) >= CROSS_BRAND_MINIMUM_COMP_COUNT:
            return cross_brand_rows
        return []

    def _comp_analysis_values(self, comps):
        self.ensure_one()
        rows = self._compatible_comp_rows(comps)
        base_values = {
            "comp_last_calculated_at": fields.Datetime.now(),
            "comp_method_version": METHOD_VERSION,
        }
        if not rows:
            unavailable_reason = (
                "missing_identity"
                if (
                    not self.manufacturer
                    or not self.model
                    or not self.equipment_type
                )
                else "missing_year"
                if not self.year
                else "missing_hours"
                if not self.hours
                else ""
            )
            return {
                **base_values,
                "comp_low": 0.0,
                "comp_median": 0.0,
                "comp_high": 0.0,
                "comp_count": 0,
                "comp_confidence": "low",
                "estimated_market_value": 0.0,
                "deal_score": 0.0,
                "grade": "verify",
                "comp_match_basis": "insufficient",
                "public_deal_summary": _public_valuation_summary(
                    0.0,
                    0.0,
                    0.0,
                    0.0,
                    0,
                    "insufficient",
                    "low",
                    unavailable_reason,
                ),
            }

        weighted = [
            (comp.price, weight)
            for comp, _score, weight, _exact, _cross_brand in rows
        ]
        low = _weighted_quantile(weighted, 0.25)
        median = _weighted_quantile(weighted, 0.50)
        high = _weighted_quantile(weighted, 0.75)
        cross_brand = all(row[4] for row in rows)
        basis = (
            "cross_brand_peer"
            if cross_brand
            else "exact_model"
            if all(row[3] for row in rows)
            else "same_make_family"
        )
        exact_count = sum(
            score >= 80 for _comp, score, _weight, _exact, _cross_brand in rows
        )
        sold_count = sum(
            comp.sale_type == "auction_result"
            for comp, _score, _weight, _exact, _cross_brand in rows
        )
        confidence = (
            "high"
            if (
                not cross_brand
                and len(rows) >= 6
                and exact_count >= 4
                and sold_count >= 3
            )
            else "medium"
        )
        ask = self.seller_ask_price or self.ask_price
        score, grade = _score_deal(ask, low, median, high, confidence)
        return {
            **base_values,
            "comp_low": low,
            "comp_median": median,
            "comp_high": high,
            "comp_count": len(rows),
            "comp_confidence": confidence,
            "estimated_market_value": median,
            "deal_score": score,
            "grade": grade,
            "comp_match_basis": basis,
            "public_deal_summary": _public_valuation_summary(
                ask, low, median, high, len(rows), basis, confidence
            ),
        }

    def _recalculate_comp_analysis(self):
        Comp = self.env["southern.equipment.comp"]
        for company in self.mapped("company_id"):
            listings = self.filtered(lambda listing: listing.company_id == company)
            comps = Comp.search([("company_id", "=", company.id), ("price", ">", 0)])
            for listing in listings:
                listing.write(listing._comp_analysis_values(comps))
        return True

    def action_recalculate_comp_analysis(self):
        self._recalculate_comp_analysis()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Comparable analysis updated"),
                "message": _("%s listing(s) recalculated.") % len(self),
                "type": "success",
                "sticky": False,
            },
        }

    def action_recalculate_all_comp_analysis(self):
        listings = self.search(
            [
                ("company_id", "=", self.env.company.id),
                ("public_status", "not in", list(TERMINAL_STATUSES)),
            ]
        )
        listings._recalculate_comp_analysis()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("All comparable analyses updated"),
                "message": _("%s active listing(s) recalculated.") % len(listings),
                "type": "success",
                "sticky": False,
            },
        }

    def _cron_recalculate_comp_analysis(self):
        listings = self.sudo().search(
            [("public_status", "not in", list(TERMINAL_STATUSES))]
        )
        listings._recalculate_comp_analysis()
        return True
