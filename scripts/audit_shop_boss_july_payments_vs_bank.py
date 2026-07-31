import csv
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAYMENTS = ROOT / "odoo_imports" / "shop_boss" / "odoo_shop_boss_july_payment_registration_results.csv"
BANK_LINES = ROOT / "odoo_imports" / "accounting" / "live_laurel_bank_statement_lines_2026_07.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_july_payment_bank_match_audit.csv"
SUMMARY = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_july_payment_bank_match_summary.md"


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def parse_date(value):
    return date.fromisoformat(str(value)[:10])


def truthy(value):
    return str(value).strip().lower() in {"true", "1", "yes"}


def candidate_status(total, bank_amount):
    if bank_amount == total:
        return "Exact bank match"
    if total > 0 and Decimal("0.00") < bank_amount < total:
        implied_fee = (total - bank_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        fee_rate = (implied_fee / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
        if implied_fee <= Decimal("1.00") or Decimal("0.0010") <= fee_rate <= Decimal("0.0500"):
            return "Possible net deposit with merchant fee"
    return ""


def main():
    payment_rows = []
    payment_groups = defaultdict(lambda: {"count": 0, "total": Decimal("0.00"), "records": []})
    for row in read_csv(PAYMENTS):
        if row["Status"] != "Registered":
            continue
        payment_rows.append(row)
        key = (row["Payment Date"], row["Journal"])
        payment_groups[key]["count"] += 1
        payment_groups[key]["total"] += money(row["Applied Amount"])
        payment_groups[key]["records"].append(f"{row['Shop Boss Type']} {row['Shop Boss Number']}")

    bank_rows = [
        row for row in read_csv(BANK_LINES)
        if row["Company"] == "Southern Equipment Company (Laurel)" and row["Journal"] == "Bank" and money(row["Amount"]) > 0
    ]
    rows = []
    for payment in sorted((row for row in payment_rows if row["Journal"] == "Bank"), key=lambda row: (row["Payment Date"], row["Shop Boss Type"], row["Shop Boss Number"])):
        pdate = parse_date(payment["Payment Date"])
        total = money(payment["Applied Amount"])
        candidates = []
        for bank in bank_rows:
            bdate = parse_date(bank["Date"])
            if bdate < pdate or (bdate - pdate).days > 7:
                continue
            bank_amount = money(bank["Amount"])
            status = candidate_status(total, bank_amount)
            if status:
                candidates.append((status, bank, bank_amount))
        if not candidates:
            rows.append({
                "Match Level": "Payment",
                "Status": "No candidate bank line",
                "Payment Date": payment["Payment Date"],
                "Payment Journal": payment["Journal"],
                "Shop Boss Payment Count": 1,
                "Shop Boss Payment Total": total,
                "Shop Boss Records": f"{payment['Shop Boss Type']} {payment['Shop Boss Number']}",
                "Bank Statement Line ID": "",
                "Bank Date": "",
                "Bank Amount": "",
                "Bank Reconciled": "",
                "Bank Ref": "",
                "Implied Fee": "",
                "Fee Rate": "",
                "Difference": "",
                "Note": "No exact or plausible net-deposit Laurel bank line within 7 days.",
            })
            continue
        for status, bank, bank_amount in candidates:
            implied_fee = (total - bank_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            fee_rate = (implied_fee / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if total else ""
            rows.append({
                "Match Level": "Payment",
                "Status": status,
                "Payment Date": payment["Payment Date"],
                "Payment Journal": payment["Journal"],
                "Shop Boss Payment Count": 1,
                "Shop Boss Payment Total": total,
                "Shop Boss Records": f"{payment['Shop Boss Type']} {payment['Shop Boss Number']}",
                "Bank Statement Line ID": bank["Bank Statement Line ID"],
                "Bank Date": bank["Date"],
                "Bank Amount": bank_amount,
                "Bank Reconciled": bank["Is Reconciled"],
                "Bank Ref": bank["Payment Ref"],
                "Implied Fee": implied_fee,
                "Fee Rate": fee_rate,
                "Difference": (bank_amount - total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                "Note": "Amount/date candidate only; already-reconciled bank lines should not be touched.",
            })

    for (payment_date, journal), data in sorted(payment_groups.items()):
        if journal != "Bank":
            continue
        pdate = parse_date(payment_date)
        total = data["total"]
        candidates = []
        for bank in bank_rows:
            bdate = parse_date(bank["Date"])
            if bdate < pdate or (bdate - pdate).days > 7:
                continue
            bank_amount = money(bank["Amount"])
            status = candidate_status(total, bank_amount)
            if not status:
                continue
            candidates.append((status, bank, bank_amount))
        if not candidates:
            rows.append({
                "Match Level": "Daily group",
                "Status": "No candidate bank line",
                "Payment Date": payment_date,
                "Payment Journal": journal,
                "Shop Boss Payment Count": data["count"],
                "Shop Boss Payment Total": total,
                "Shop Boss Records": "; ".join(data["records"]),
                "Bank Statement Line ID": "",
                "Bank Date": "",
                "Bank Amount": "",
                "Bank Reconciled": "",
                "Bank Ref": "",
                "Implied Fee": "",
                "Fee Rate": "",
                "Difference": "",
                "Note": "No exact or plausible net-deposit Laurel bank line within 5 days.",
            })
            continue
        for status, bank, bank_amount in candidates:
            implied_fee = (total - bank_amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            fee_rate = (implied_fee / total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP) if total else ""
            rows.append({
                "Match Level": "Daily group",
                "Status": status,
                "Payment Date": payment_date,
                "Payment Journal": journal,
                "Shop Boss Payment Count": data["count"],
                "Shop Boss Payment Total": total,
                "Shop Boss Records": "; ".join(data["records"]),
                "Bank Statement Line ID": bank["Bank Statement Line ID"],
                "Bank Date": bank["Date"],
                "Bank Amount": bank_amount,
                "Bank Reconciled": bank["Is Reconciled"],
                "Bank Ref": bank["Payment Ref"],
                "Implied Fee": implied_fee,
                "Fee Rate": fee_rate,
                "Difference": (bank_amount - total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
                "Note": "Amount/date candidate only; already-reconciled bank lines should not be touched.",
            })

    fields = [
        "Match Level", "Status", "Payment Date", "Payment Journal", "Shop Boss Payment Count",
        "Shop Boss Payment Total", "Shop Boss Records", "Bank Statement Line ID",
        "Bank Date", "Bank Amount", "Bank Reconciled", "Bank Ref", "Implied Fee",
        "Fee Rate", "Difference", "Note",
    ]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

    payment_level = [row for row in rows if row["Match Level"] == "Payment"]
    payment_keys = {(row["Payment Date"], row["Shop Boss Records"]) for row in payment_level}
    no_candidate_keys = {
        (row["Payment Date"], row["Shop Boss Records"])
        for row in payment_level
        if row["Status"] == "No candidate bank line"
    }
    candidate_keys = payment_keys - no_candidate_keys
    already_reconciled_keys = {
        (row["Payment Date"], row["Shop Boss Records"])
        for row in payment_level
        if row.get("Bank Reconciled") and truthy(row["Bank Reconciled"])
    }
    bank_total = sum((data["total"] for (payment_date, journal), data in payment_groups.items() if journal == "Bank"), Decimal("0.00"))
    cash_total = sum((data["total"] for (payment_date, journal), data in payment_groups.items() if journal == "Cash"), Decimal("0.00"))
    SUMMARY.write_text(
        "\n".join([
            "# Shop Boss July Payments vs Bank Audit",
            "",
            "Scope: July 2026 Shop Boss payments registered in Odoo compared to all Laurel bank statement lines, including reconciled lines.",
            "",
            "## Results",
            "",
            f"- Bank-journal Shop Boss payment total: `${bank_total:,.2f}`",
            f"- Cash-journal Shop Boss payment total: `${cash_total:,.2f}`",
            f"- Bank payment rows reviewed: {sum(1 for row in payment_rows if row['Journal'] == 'Bank')}",
            f"- Payment rows with amount/fee-based bank candidates: {len(candidate_keys)}",
            f"- Payment rows with no candidate bank line: {len(no_candidate_keys)}",
            f"- Payment rows with candidates already reconciled in bank: {len(already_reconciled_keys)}",
            "",
            "## Interpretation",
            "",
            "This audit is amount/date/fee based only. It does not apply reconciliation entries.",
            "Merchant service fees explain why several card deposits do not equal the Shop Boss gross payment amount.",
            "",
            "## Files",
            "",
            "- Detail: `odoo_imports/shop_boss/shop_boss_july_payment_bank_match_audit.csv`",
            "",
        ]),
        encoding="utf-8",
    )
    print(f"Rows: {len(rows)}")
    print(f"Output: {OUT}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    main()
