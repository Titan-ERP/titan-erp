import re

from odoo import _, fields, models


BROKER_GROUPS = (
    "southern_equipment_brokerage.group_southern_equipment_admin,"
    "southern_equipment_brokerage.group_southern_deal_broker"
)
MAX_COMP_HOURS_DIFFERENCE = 500.0
MAX_COMP_YEAR_DIFFERENCE = 3
GOOD_MAX_MEDIAN_MULTIPLIER = 1.10
METHOD_VERSION = "native-v1-one-comp-500-hours-3-years-110pct"
TERMINAL_STATUSES = ("archived", "sold")


def _normalized(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _model_family(manufacturer, model):
    family = _normalized(model)
    if _normalized(manufacturer) in {"cat", "caterpillar"}:
        family = re.sub(r"cr$", "", family)
    return family


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


class SouthernEquipmentListingCompAnalysis(models.Model):
    _inherit = "southern.equipment.listing"

    comp_last_calculated_at = fields.Datetime(
        string="Comp Analysis Updated", readonly=True, groups=BROKER_GROUPS
    )
    comp_method_version = fields.Char(
        string="Comp Method", readonly=True, groups=BROKER_GROUPS
    )

    def _compatible_comp_rows(self, comps):
        self.ensure_one()
        listing_make = _normalized(self.manufacturer)
        listing_model = _normalized(self.model)
        listing_type = _normalized(self.equipment_type)
        if not listing_make or not listing_model or not listing_type:
            return []
        listing_family = _model_family(self.manufacturer, self.model)
        rows = []
        today = fields.Date.context_today(self)
        for comp in comps:
            if comp.price <= 0 or _normalized(comp.manufacturer) != listing_make:
                continue
            if _normalized(comp.equipment_type) != listing_type:
                continue
            comp_model = _normalized(comp.model)
            exact = comp_model == listing_model
            family = (
                bool(listing_family)
                and listing_family == _model_family(comp.manufacturer, comp.model)
            )
            if not exact and not family:
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

            similarity = 80.0 if exact else 75.0
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
                rows.append((comp, min(similarity, 100.0), weight, exact))
        return rows

    def _comp_analysis_values(self, comps):
        self.ensure_one()
        rows = self._compatible_comp_rows(comps)
        base_values = {
            "comp_last_calculated_at": fields.Datetime.now(),
            "comp_method_version": METHOD_VERSION,
        }
        if not rows:
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
            }

        weighted = [(comp.price, weight) for comp, _score, weight, _exact in rows]
        low = _weighted_quantile(weighted, 0.25)
        median = _weighted_quantile(weighted, 0.50)
        high = _weighted_quantile(weighted, 0.75)
        exact_count = sum(score >= 80 for _comp, score, _weight, _exact in rows)
        sold_count = sum(
            comp.sale_type == "auction_result"
            for comp, _score, _weight, _exact in rows
        )
        confidence = (
            "high"
            if len(rows) >= 6 and exact_count >= 4 and sold_count >= 3
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

    def _cron_recalculate_comp_analysis(self):
        listings = self.sudo().search(
            [("public_status", "not in", list(TERMINAL_STATUSES))]
        )
        listings._recalculate_comp_analysis()
        return True
