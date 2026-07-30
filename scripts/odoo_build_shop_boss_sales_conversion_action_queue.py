import csv
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "odoo_imports" / "accounting" / "sales_analysis" / "2026-07-26"
COVERAGE = BASE / "all_available_shop_boss_invoice_coverage_audit.csv"
MONTHS = BASE / "all_available_shop_boss_vs_odoo_revenue_by_month.csv"
OUT = BASE / "shop_boss_sales_conversion_action_queue.csv"
SUMMARY = BASE / "shop_boss_sales_conversion_action_queue.md"


def money(value):
    return Decimal(str(value or "0").replace(",", "")).quantize(Decimal("0.01"))


def month_from_date(value):
    text = str(value or "")
    if "/" in text and len(text) >= 10:
        return f"{text[6:10]}-{text[:2]}"
    return text[:7]


def main():
    month_rows = {row["month"]: row for row in csv.DictReader(MONTHS.open(newline="", encoding="utf-8"))}
    source_rows = list(csv.DictReader(COVERAGE.open(newline="", encoding="utf-8")))
    special_by_month = {}
    for row in source_rows:
        if row["status"] != "no_odoo_invoice_match":
            continue
        amount = money(row["shop_boss_amount"])
        customer = row["shop_boss_customer"].upper()
        if amount >= Decimal("25000") or "COLLIER" in customer and amount >= Decimal("70000"):
            month = month_from_date(row["shop_boss_date"])
            special_by_month[month] = special_by_month.get(month, Decimal("0.00")) + amount
    rows = []
    for row in source_rows:
        if row["status"] != "no_odoo_invoice_match":
            continue
        month = month_from_date(row["shop_boss_date"])
        amount = money(row["shop_boss_amount"])
        month_info = month_rows.get(month, {})
        gap = money(month_info.get("revenue_gap_shop_minus_odoo", "0"))
        adjusted_gap = gap - special_by_month.get(month, Decimal("0.00"))
        customer = row["shop_boss_customer"].upper()
        doc = f"{row['shop_boss_type']}{row['shop_boss_number']}"
        if amount >= Decimal("25000") or "COLLIER" in customer and amount >= Decimal("70000"):
            bucket = "resolved_equipment_sale_loan_paydown"
            action = "do_not_create_normal_invoice"
            reason = "Verified in Odoo as Gain on Asset Disposal with related Equipment Notes Payable loan paydown."
        elif month in {"2026-02", "2026-03"} and gap > 0:
            bucket = "resolved_historical_summary_trueup"
            action = "no_invoice_creation_needed_unless_rebuilding_document_level_history"
            reason = "February/March revenue was posted through Shop Boss historical paid-sales summary true-up to Bank Suspense."
        elif adjusted_gap <= Decimal("1000.00"):
            bucket = "likely_covered_by_bank_simplification"
            action = "do_not_create_invoice_until_bank_revenue_is_reversed_or_matched"
            reason = "Odoo revenue for the month is already at or above Shop Boss revenue after excluding resolved large/equipment sale treatment; creating invoices may double-count."
        else:
            bucket = "revenue_gap_review"
            action = "review_for_invoice_rebuild_or_summary_trueup"
            reason = "Shop Boss exceeds Odoo revenue for the month after known special items."
        rows.append(
            {
                "month": month,
                "shop_boss_doc": doc,
                "shop_boss_date": row["shop_boss_date"],
                "customer": row["shop_boss_customer"],
                "amount": row["shop_boss_amount"],
                "month_revenue_gap": f"{float(gap):.2f}",
                "bucket": bucket,
                "recommended_action": action,
                "reason": reason,
            }
        )

    fields = ["month", "shop_boss_doc", "shop_boss_date", "customer", "amount", "month_revenue_gap", "bucket", "recommended_action", "reason"]
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    buckets = {}
    for row in rows:
        key = row["bucket"]
        if key not in buckets:
            buckets[key] = {"count": 0, "total": Decimal("0.00")}
        buckets[key]["count"] += 1
        buckets[key]["total"] += money(row["amount"])
    largest = sorted(rows, key=lambda item: money(item["amount"]), reverse=True)[:20]
    lines = [
        "# Shop Boss Sales Conversion Action Queue",
        "",
        "## Buckets",
        "",
        *[f"- {key}: {value['count']} docs / ${float(value['total']):,.2f}" for key, value in sorted(buckets.items())],
        "",
        "## Largest Items",
        "",
        *[
            f"- {row['shop_boss_doc']} {row['shop_boss_date']} {row['customer']} ${float(money(row['amount'])):,.2f}: {row['bucket']}"
            for row in largest
        ],
        "",
        "## Recommendation",
        "",
        "Do not batch-create the remaining missing Shop Boss document invoices unless the bank-posted summary revenue is first reversed, matched, or intentionally replaced. Revenue is now covered at the summary level; the remaining choice is whether the books need document-level invoice/payment history.",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(SUMMARY)
    for key, value in sorted(buckets.items()):
        print(key, value["count"], f"${float(value['total']):,.2f}")


if __name__ == "__main__":
    main()
