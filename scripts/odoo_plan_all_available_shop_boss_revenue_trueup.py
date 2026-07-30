import csv
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IN = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "all_available_shop_boss_vs_odoo_revenue_by_month.csv"
OUT = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "all_available_shop_boss_revenue_trueup_plan.csv"
SUMMARY = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26" / "all_available_shop_boss_revenue_trueup_plan.md"


def money(value):
    return Decimal(str(value or "0").replace(",", "")).quantize(Decimal("0.01"))


def main():
    rows = list(csv.DictReader(IN.open(newline="", encoding="utf-8")))
    plan = []
    for row in rows:
        month = row["month"]
        shop_parts = money(row["shop_boss_parts"])
        shop_service = money(row["shop_boss_service"])
        odoo_parts = money(row["odoo_parts"])
        odoo_service = money(row["odoo_service"])
        odoo_rental = money(row["odoo_rental"])
        notes = []
        parts_gap = shop_parts - odoo_parts
        service_gap = shop_service - odoo_service
        # Rental is kept out of the Shop Boss sales true-up because the existing
        # rental evidence came from equipment/rental identification, not the
        # finalized RO/part-sale exports used here.
        if parts_gap > 0:
            plan.append(
                {
                    "month": month,
                    "account": "Parts Revenue",
                    "action": "increase_revenue",
                    "amount": f"{float(parts_gap):.2f}",
                    "reason": "Shop Boss parts revenue exceeds Odoo posted parts revenue.",
                }
            )
        elif parts_gap < 0:
            notes.append(f"Parts over Odoo vs Shop Boss by ${float(-parts_gap):,.2f}")
        if service_gap > 0:
            plan.append(
                {
                    "month": month,
                    "account": "Service Revenue",
                    "action": "increase_revenue",
                    "amount": f"{float(service_gap):.2f}",
                    "reason": "Shop Boss service revenue exceeds Odoo posted service revenue.",
                }
            )
        elif service_gap < 0:
            notes.append(f"Service over Odoo vs Shop Boss by ${float(-service_gap):,.2f}")
        if odoo_rental:
            notes.append(f"Odoo rental revenue kept separate: ${float(odoo_rental):,.2f}")
        if notes:
            plan.append(
                {
                    "month": month,
                    "account": "Review Only",
                    "action": "do_not_auto_adjust_down",
                    "amount": "0.00",
                    "reason": " ; ".join(notes),
                }
            )

    OUT.parent.mkdir(parents=True, exist_ok=True)
    fields = ["month", "account", "action", "amount", "reason"]
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(plan)

    increases = [row for row in plan if row["action"] == "increase_revenue"]
    by_month = {}
    for row in increases:
        by_month[row["month"]] = by_month.get(row["month"], Decimal("0.00")) + money(row["amount"])
    lines = [
        "# All Available Shop Boss Revenue True-Up Plan",
        "",
        f"- Revenue increase rows: {len(increases)}",
        f"- Total proposed revenue increase: ${float(sum((money(row['amount']) for row in increases), Decimal('0.00'))):,.2f}",
        "",
        "## Proposed Increases",
        "",
        *[
            f"- {row['month']} {row['account']}: ${float(money(row['amount'])):,.2f}"
            for row in increases
        ],
        "",
        "## Month Totals",
        "",
        *[f"- {month}: ${float(amount):,.2f}" for month, amount in sorted(by_month.items())],
        "",
        "## Review Notes",
        "",
        *[
            f"- {row['month']}: {row['reason']}"
            for row in plan
            if row["action"] == "do_not_auto_adjust_down"
        ],
    ]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(SUMMARY)
    print(f"Proposed increase ${float(sum((money(row['amount']) for row in increases), Decimal('0.00'))):,.2f}")


if __name__ == "__main__":
    main()
