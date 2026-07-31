import csv
import re
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IN_FILE = ROOT / "odoo_imports" / "bank_reconciliation" / "odoo_unreconciled_bank_statement_lines_laurel_bank_live.csv"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
UNKNOWN_SUMMARY = OUT / "bank_reconciliation_remaining_unknowns.csv"


KNOWN_REVIEW = [
    (r"CHECK\s+\d+\s+-", "Identified check", "Payee/check number found from statement image; now needs account/vendor decision."),
    (r"BANKCARD.*MTOT DEP|DEPOSIT", "Card/customer deposit", "Needs matching to POS/sales/payment batch."),
    (r"TELEPHONE TRF TO LN|ATS - CHECKING TO LN|LOAN", "Loan transfer/payment", "Needs principal/interest split or loan account."),
    (r"IRS|MSDEPTOFREVENUE|TAXPAYMENT", "Tax payment", "Needs exact tax liability account."),
    (r"UPS|FEDEX|USPS", "Shipping", "No clear Laurel shipping expense account found."),
    (r"\bCHECK\b|TELLER CHECK", "Check", "Needs check number/payee or bank statement image detail."),
    (r"PAYROLL|QUICKBOOKS|INTUIT", "Payroll", "Needs payroll clearing/liability mapping."),
    (r"DISCOVER|CAPITAL ONE|CHASE|AMEX|CREDIT CARD", "Credit card payment", "Needs credit card liability account."),
    (r"AFCO", "Insurance/finance payment", "Needs vendor/account confirmation."),
]


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def merchant_key(ref):
    text = str(ref or "").upper()
    text = re.sub(r"\*+\d+", " ", text)
    text = re.sub(r"\b\d{2}/\d{2}\b.*$", " ", text)
    text = re.sub(r"\b\d{1,2}:\d{2}\b", " ", text)
    text = re.sub(r"\b(POS PURCHASE|NON PIN|PIN|DEBIT|CARD|PURCHASE|CHECKCARD)\b", " ", text)
    text = re.sub(r"[^A-Z0-9&/# -]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return " ".join(text.split()[:6]) or "Unclear"


def classify(ref):
    text = str(ref or "").upper()
    for pattern, reason, need in KNOWN_REVIEW:
        if re.search(pattern, text):
            return reason, need
    return "Unknown merchant/reference", "Needs payee/category decision or vendor bill/payment match."


def main():
    rows = []
    with IN_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))

    groups = defaultdict(list)
    for row in rows:
        reason, need = classify(row.get("payment_ref", ""))
        key = merchant_key(row.get("payment_ref", ""))
        groups[(reason, need, key)].append(row)

    summary = []
    for (reason, need, key), group_rows in groups.items():
        total = sum(money(row["amount"]) for row in group_rows)
        summary.append(
            {
                "Reason": reason,
                "Need": need,
                "Merchant Key": key,
                "Count": len(group_rows),
                "Net Amount": float(total),
                "Example": group_rows[0].get("payment_ref", ""),
            }
        )
    summary.sort(key=lambda row: (row["Reason"], -row["Count"], row["Merchant Key"]))

    with UNKNOWN_SUMMARY.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Reason", "Need", "Merchant Key", "Count", "Net Amount", "Example"],
        )
        writer.writeheader()
        writer.writerows(summary)

    reason_counts = defaultdict(lambda: [0, Decimal("0")])
    for row in rows:
        reason, need = classify(row.get("payment_ref", ""))
        reason_counts[(reason, need)][0] += 1
        reason_counts[(reason, need)][1] += money(row["amount"])

    print(f"Remaining Laurel Bank unreconciled lines: {len(rows)}")
    for (reason, need), (count, total) in sorted(reason_counts.items(), key=lambda item: -item[1][0]):
        print(f"{count:>4} {float(total):>12.2f}  {reason} - {need}")
    print(f"Summary: {UNKNOWN_SUMMARY}")


if __name__ == "__main__":
    main()
