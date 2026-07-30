import csv
import re
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHOP_DIR = ROOT / "odoo_imports" / "shop_boss"
ACCOUNTING_DIR = ROOT / "odoo_imports" / "accounting"
DETAIL_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports" / "operational_details"

SHOP_AR = SHOP_DIR / "shop_boss_ar_open_2026_07.csv"
SHOP_PAYMENTS = SHOP_DIR / "shop_boss_payments_received_2026_07.csv"
ODOO_INVOICES = ACCOUNTING_DIR / "odoo_july_invoice_payment_export.csv"
ODOO_BANK = ACCOUNTING_DIR / "live_unreconciled_bank_statement_lines_2026_07.csv"
ODOO_TO_INVOICE = DETAIL_DIR / "sales_orders_to_invoice.csv"

OUT_DIR = ROOT / "odoo_imports" / "shop_boss"
INVOICE_CROSSWALK = OUT_DIR / "shop_boss_odoo_invoice_crosswalk_2026_07.csv"
PAYMENT_BANK_CROSSWALK = OUT_DIR / "shop_boss_odoo_payment_bank_crosswalk_2026_07.csv"
SUMMARY = OUT_DIR / "shop_boss_odoo_july_fix_summary.md"


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cents(value):
    return int(money(value) * 100)


def norm(value):
    text = str(value or "").upper()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def contains_number(row, number):
    haystack = " ".join(str(row.get(k, "")) for k in ["name", "invoice_origin", "ref", "narration"])
    return bool(re.search(rf"(^|[^0-9]){re.escape(str(number))}([^0-9]|$)", haystack))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def match_shop_ar_to_odoo(shop_ar, invoices):
    by_amount = defaultdict(list)
    for inv in invoices:
        by_amount[cents(inv.get("amount_total"))].append(inv)

    rows = []
    for shop in shop_ar:
        candidates = []
        for inv in by_amount.get(cents(shop.get("total")), []):
            score = 0
            reasons = []
            if norm(shop.get("customer")) and norm(shop.get("customer")) in norm(inv.get("partner_id")):
                score += 60
                reasons.append("customer substring")
            if contains_number(inv, shop.get("number")):
                score += 80
                reasons.append("Shop Boss number in Odoo origin/ref")
            if inv.get("invoice_date") == "2026-" + shop.get("date", "")[6:10] + "-" + shop.get("date", "")[:2]:
                score += 20
                reasons.append("same date")
            candidates.append((score, reasons, inv))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates and candidates[0][0] >= 80 and (len(candidates) == 1 or candidates[0][0] > candidates[1][0]):
            score, reasons, inv = candidates[0]
            status = "Matched"
        elif candidates:
            score, reasons, inv = candidates[0]
            status = "Review"
        else:
            score, reasons, inv = 0, [], {}
            status = "No Odoo invoice match"
        rows.append(
            {
                "Status": status,
                "Confidence": score,
                "Reasons": "; ".join(reasons),
                "Shop Boss Type": shop.get("type"),
                "Shop Boss Number": shop.get("number"),
                "Shop Boss Date": shop.get("date"),
                "Shop Boss Customer": shop.get("customer"),
                "Shop Boss Total": shop.get("total"),
                "Shop Boss Balance": shop.get("balance"),
                "Odoo Invoice ID": inv.get("id", ""),
                "Odoo Invoice": inv.get("name", ""),
                "Odoo Customer": inv.get("partner_id", ""),
                "Odoo Date": inv.get("invoice_date", ""),
                "Odoo Total": inv.get("amount_total", ""),
                "Odoo Residual": inv.get("amount_residual", ""),
                "Odoo Payment State": inv.get("payment_state", ""),
                "Odoo Origin": inv.get("invoice_origin", ""),
                "Odoo Ref": inv.get("ref", ""),
            }
        )
    return rows


def match_payments_to_bank(payments, bank_lines):
    by_amount = defaultdict(list)
    for bank in bank_lines:
        by_amount[abs(cents(bank.get("Amount")))].append(bank)

    rows = []
    for payment in payments:
        candidates = []
        pay_date = "2026-" + payment.get("payment_date", "")[6:10] + "-" + payment.get("payment_date", "")[:2]
        for bank in by_amount.get(cents(payment.get("amount")), []):
            score = 0
            reasons = []
            if bank.get("Date") == pay_date:
                score += 50
                reasons.append("same date")
            if norm(payment.get("customer")) and norm(payment.get("customer")) in norm(bank.get("Payment Ref") + " " + bank.get("Partner")):
                score += 50
                reasons.append("customer in bank text")
            payment_type = norm(payment.get("payment_type"))
            bank_text = norm(bank.get("Payment Ref") + " " + bank.get("Bucket"))
            if payment_type and any(token in bank_text for token in payment_type.split()):
                score += 20
                reasons.append("payment type in bank text")
            candidates.append((score, reasons, bank))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates and candidates[0][0] >= 70 and (len(candidates) == 1 or candidates[0][0] > candidates[1][0]):
            score, reasons, bank = candidates[0]
            status = "Matched"
        elif candidates:
            score, reasons, bank = candidates[0]
            status = "Review"
        else:
            score, reasons, bank = 0, [], {}
            status = "No exact open bank-line match"
        rows.append(
            {
                "Status": status,
                "Confidence": score,
                "Reasons": "; ".join(reasons),
                "Shop Boss Type": payment.get("type"),
                "Shop Boss Number": payment.get("number"),
                "Shop Boss Customer": payment.get("customer"),
                "Payment Date": payment.get("payment_date"),
                "Payment Type": payment.get("payment_type"),
                "Amount": payment.get("amount"),
                "Bank Statement Line ID": bank.get("Bank Statement Line ID", ""),
                "Bank Date": bank.get("Date", ""),
                "Bank Amount": bank.get("Amount", ""),
                "Bank Partner": bank.get("Partner", ""),
                "Bank Ref": bank.get("Payment Ref", ""),
                "Bank Bucket": bank.get("Bucket", ""),
            }
        )
    return rows


def main():
    shop_ar = read_csv(SHOP_AR)
    payments = read_csv(SHOP_PAYMENTS)
    invoices = read_csv(ODOO_INVOICES)
    bank_lines = read_csv(ODOO_BANK)
    to_invoice = [
        row for row in read_csv(ODOO_TO_INVOICE)
        if str(row.get("date_order", "")).startswith("2026-07")
    ]

    invoice_rows = match_shop_ar_to_odoo(shop_ar, invoices)
    payment_rows = match_payments_to_bank(payments, bank_lines)

    write_csv(
        INVOICE_CROSSWALK,
        invoice_rows,
        [
            "Status", "Confidence", "Reasons", "Shop Boss Type", "Shop Boss Number", "Shop Boss Date",
            "Shop Boss Customer", "Shop Boss Total", "Shop Boss Balance", "Odoo Invoice ID", "Odoo Invoice",
            "Odoo Customer", "Odoo Date", "Odoo Total", "Odoo Residual", "Odoo Payment State",
            "Odoo Origin", "Odoo Ref",
        ],
    )
    write_csv(
        PAYMENT_BANK_CROSSWALK,
        payment_rows,
        [
            "Status", "Confidence", "Reasons", "Shop Boss Type", "Shop Boss Number", "Shop Boss Customer",
            "Payment Date", "Payment Type", "Amount", "Bank Statement Line ID", "Bank Date", "Bank Amount",
            "Bank Partner", "Bank Ref", "Bank Bucket",
        ],
    )

    invoice_counts = defaultdict(int)
    for row in invoice_rows:
        invoice_counts[row["Status"]] += 1
    payment_counts = defaultdict(int)
    for row in payment_rows:
        payment_counts[row["Status"]] += 1

    SUMMARY.write_text(
        "\n".join(
            [
                "# Shop Boss to Odoo July Fix Summary",
                "",
                "Scope: July 2026 only.",
                "",
                "## Source Counts",
                "",
                f"- Shop Boss open July AR rows: {len(shop_ar)}",
                f"- Shop Boss July payment rows: {len(payments)}",
                f"- Odoo July customer invoices: {len(invoices)}",
                f"- Odoo July sales orders still to invoice: {len(to_invoice)}",
                f"- Odoo July unreconciled bank lines: {len(bank_lines)}",
                "",
                "## Match Results",
                "",
                f"- Open AR invoice crosswalk: {dict(invoice_counts)}",
                f"- Payment-to-bank crosswalk: {dict(payment_counts)}",
                "",
                "## Files",
                "",
                f"- Invoice crosswalk: `{INVOICE_CROSSWALK.relative_to(ROOT).as_posix()}`",
                f"- Payment/bank crosswalk: `{PAYMENT_BANK_CROSSWALK.relative_to(ROOT).as_posix()}`",
                "",
                "## Guardrail",
                "",
                "No Odoo changes are applied by this script. Use these files as the evidence queue for safe invoice creation, payment registration, or bank reconciliation.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Invoice crosswalk rows: {len(invoice_rows)} {dict(invoice_counts)}")
    print(f"Payment bank crosswalk rows: {len(payment_rows)} {dict(payment_counts)}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    main()
