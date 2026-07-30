import csv
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MATCH_AUDIT = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_odoo_all_invoice_match_audit_2026_07.csv"
ODOO_INVOICES = ROOT / "odoo_imports" / "accounting" / "odoo_july_invoice_payment_export.csv"
OUT = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_odoo_payment_status_audit_2026_07.csv"
SUMMARY = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_odoo_payment_status_summary_2026_07.md"


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def expected_state(row):
    total = money(row["Total"])
    paid = money(row["Payments"])
    if paid <= 0:
        return "Open / unpaid in Shop Boss"
    if paid < total:
        return "Partially paid in Shop Boss"
    return "Paid in Shop Boss"


def audit_status(expected, invoice):
    payment_state = invoice.get("payment_state", "")
    residual = money(invoice.get("amount_residual"))
    if expected == "Open / unpaid in Shop Boss":
        return "Aligned" if payment_state == "not_paid" and residual > 0 else "Needs Odoo payment update"
    if expected == "Partially paid in Shop Boss":
        return "Aligned" if payment_state in {"partial", "in_payment"} else "Needs Odoo payment update"
    return "Aligned" if payment_state in {"paid", "in_payment"} else "Needs Odoo payment update"


def main():
    invoices = {row["id"]: row for row in read_csv(ODOO_INVOICES) if row.get("move_type") == "out_invoice"}
    rows = []
    for match in read_csv(MATCH_AUDIT):
        if match["Status"] != "Confirmed":
            continue
        invoice = invoices.get(match["Odoo Invoice ID"], {})
        expected = expected_state(match)
        rows.append({
            "Status": audit_status(expected, invoice),
            "Expected": expected,
            "Shop Boss Type": match["Shop Boss Type"],
            "Shop Boss Number": match["Shop Boss Number"],
            "Shop Boss Customer": match["Shop Boss Customer"],
            "Shop Boss Total": match["Total"],
            "Shop Boss Payments": match["Payments"],
            "Shop Boss Payment Source": match["Payment Source"],
            "Odoo Invoice ID": match["Odoo Invoice ID"],
            "Odoo Invoice": invoice.get("name", match.get("Odoo Invoice", "")),
            "Odoo Payment State": invoice.get("payment_state", ""),
            "Odoo Total": invoice.get("amount_total", ""),
            "Odoo Residual": invoice.get("amount_residual", ""),
            "Odoo Ref": invoice.get("ref", ""),
        })

    fields = [
        "Status", "Expected", "Shop Boss Type", "Shop Boss Number", "Shop Boss Customer",
        "Shop Boss Total", "Shop Boss Payments", "Shop Boss Payment Source", "Odoo Invoice ID",
        "Odoo Invoice", "Odoo Payment State", "Odoo Total", "Odoo Residual", "Odoo Ref",
    ]
    write_csv(OUT, rows, fields)
    counts = Counter(row["Status"] for row in rows)
    expected_counts = Counter(row["Expected"] for row in rows)
    SUMMARY.write_text(
        "\n".join([
            "# Shop Boss / Odoo Payment Status Audit",
            "",
            "Scope: July 2026 Shop Boss finalized/closed invoices matched to live Odoo customer invoices.",
            "",
            "## Results",
            "",
            f"- Shop Boss matched invoices checked: {len(rows)}",
            f"- Needs Odoo payment update: {counts.get('Needs Odoo payment update', 0)}",
            f"- Aligned: {counts.get('Aligned', 0)}",
            "",
            "## Expected States",
            "",
            f"- Paid in Shop Boss: {expected_counts.get('Paid in Shop Boss', 0)}",
            f"- Partially paid in Shop Boss: {expected_counts.get('Partially paid in Shop Boss', 0)}",
            f"- Open / unpaid in Shop Boss: {expected_counts.get('Open / unpaid in Shop Boss', 0)}",
            "",
            "## Files",
            "",
            "- Detail: `odoo_imports/shop_boss/shop_boss_odoo_payment_status_audit_2026_07.csv`",
            "",
        ]),
        encoding="utf-8",
    )
    print(f"Rows: {len(rows)}")
    print(f"Aligned: {counts.get('Aligned', 0)}")
    print(f"Needs Odoo payment update: {counts.get('Needs Odoo payment update', 0)}")
    print(f"Output: {OUT}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    main()
