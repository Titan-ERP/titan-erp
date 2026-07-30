import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
IN_FILE = ROOT / "odoo_imports" / "bank_reconciliation" / "shop_boss_june_deposit_reconcile_review_plan.csv"
OUT_FILE = ROOT / "odoo_imports" / "bank_reconciliation" / "shop_boss_june_deposit_review_decisions.csv"

ALREADY_RECONCILED = {"449", "457", "465"}


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def main():
    rows = []
    with IN_FILE.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            bank_id = row["Bank Line ID"]
            diff = money(row["Bank vs Estimated Net"])
            source_after_bank = row["Source Date"] > row["Bank Date"]
            if bank_id in ALREADY_RECONCILED:
                decision = "Exclude - already reconciled"
                reason = "Odoo live check shows this bank line is already reconciled."
            elif source_after_bank:
                decision = "Hold"
                reason = "Source/status date is after the bank deposit date; possible, but not strong enough for automatic reconciliation."
            elif row["Match Source"] == "Exact" and diff == 0:
                decision = "Approve"
                reason = "Exact amount match with source date on/before bank date."
            elif row["Match Source"] == "Fee Adjusted Subset" and abs(diff) <= Decimal("1.00"):
                decision = "Approve"
                reason = "Gross source records less 3.5% fee matches bank deposit within $1.00."
            else:
                decision = "Review"
                reason = "Potential match, but fee/date pattern needs human review."
            row["Decision"] = decision
            row["Decision Reason"] = reason
            rows.append(row)

    with OUT_FILE.open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["Decision", "Decision Reason"] + [name for name in rows[0].keys() if name not in {"Decision", "Decision Reason"}]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    groups = defaultdict(int)
    for row in rows:
        groups[row["Decision"]] += 1
    print(f"Decision file: {OUT_FILE}")
    for decision, count in sorted(groups.items()):
        print(f"{decision}: {count}")
    print()
    for row in rows:
        print(
            f"{row['Decision']}: bank {row['Bank Line ID']} {row['Bank Date']} "
            f"${row['Bank Amount']} <- {row['Source Records']} "
            f"gross ${row['Gross Shop Boss']} fee ${row['Estimated Merchant Fee']} "
            f"diff ${row['Bank vs Estimated Net']} | {row['Decision Reason']}"
        )


if __name__ == "__main__":
    main()
