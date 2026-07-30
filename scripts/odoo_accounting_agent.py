import argparse
import csv
import subprocess
import sys
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTING_DIR = ROOT / "odoo_imports" / "accounting"
DETAIL_DIR = ROOT / "odoo_imports" / "product_master" / "review_reports" / "operational_details"
CHECKLIST_PATH = ACCOUNTING_DIR / "month_end_accounting_checklist.md"
OVERDUE_PATH = DETAIL_DIR / "accounting_overdue_documents.csv"
TO_INVOICE_PATH = DETAIL_DIR / "sales_orders_to_invoice.csv"
MISSING_TAX_PATH = DETAIL_DIR / "accounting_sale_products_missing_tax.csv"
PARTS_VERIFY_PATH = DETAIL_DIR / "accounting_parts_category_accounts.csv"
DEFAULT_MONTH = "2026-07"


def run_step(args, label):
    print(f"\n== {label} ==")
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout.rstrip())
    if result.stderr:
        print(result.stderr.rstrip(), file=sys.stderr)
    if result.returncode != 0:
        raise SystemExit(f"{label} failed with exit code {result.returncode}.")
    return result.stdout


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def month_bounds(month):
    year, month_number = [int(part) for part in month.split("-", 1)]
    if month_number == 12:
        return f"{year}-12-01", f"{year + 1}-01-01"
    return f"{year}-{month_number:02d}-01", f"{year}-{month_number + 1:02d}-01"


def month_label(month):
    year, month_number = month.split("-", 1)
    names = {
        "01": "January",
        "02": "February",
        "03": "March",
        "04": "April",
        "05": "May",
        "06": "June",
        "07": "July",
        "08": "August",
        "09": "September",
        "10": "October",
        "11": "November",
        "12": "December",
    }
    return f"{names[month_number]} {year}"


def dated_recon_paths(month):
    if not month:
        return (
            ACCOUNTING_DIR / "live_unreconciled_bank_statement_lines.csv",
            ACCOUNTING_DIR / "live_reconciliation_queue_summary.csv",
        )
    suffix = month.replace("-", "_")
    return (
        ACCOUNTING_DIR / f"live_unreconciled_bank_statement_lines_{suffix}.csv",
        ACCOUNTING_DIR / f"live_reconciliation_queue_summary_{suffix}.csv",
    )


def in_month(value, month, date_only=False):
    if not month:
        return True
    start, end = month_bounds(month)
    text = str(value or "")
    if date_only:
        text = text[:10]
    return bool(text) and start <= text < end


def money(value):
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))


def sum_field(rows, field):
    total = Decimal("0.00")
    for row in rows:
        total += money(row.get(field))
    return total


def bucket_totals(rows):
    totals = {}
    for row in rows:
        bucket = row.get("Bucket") or "Unbucketed"
        count, amount = totals.get(bucket, (0, Decimal("0.00")))
        totals[bucket] = (count + int(row.get("Count") or 0), amount + money(row.get("Net Amount")))
    return dict(sorted(totals.items(), key=lambda item: (-item[1][0], item[0])))


def format_money(value):
    value = money(value)
    if value < 0:
        return f"-${abs(value):,.2f}"
    return f"${value:,.2f}"


def parts_drift_count(rows):
    drift = 0
    for row in rows:
        name = row.get("complete_name") or ""
        if not name.startswith("Parts /"):
            continue
        income = row.get("property_account_income_categ_id") or ""
        expense = row.get("property_account_expense_categ_id") or ""
        if income != "Parts Revenue" or expense != "Parts COGS":
            drift += 1
    return drift


def write_checklist(summary):
    ACCOUNTING_DIR.mkdir(parents=True, exist_ok=True)
    bucket_lines = []
    for bucket, (count, amount) in summary["buckets"].items():
        bucket_lines.append(f"- {bucket}: {count} lines, net `{format_money(amount)}`")
    bucket_text = "\n".join(bucket_lines) if bucket_lines else "- No unreconciled bank lines found."

    CHECKLIST_PATH.write_text(
        f"""# Odoo Accounting Agent Report

Generated from the latest live Odoo refresh.

Focus period: {summary['period']}.

## Agent Run

- Odoo write performed: {'yes, category fixes applied' if summary['applied'] else 'no'}
- Parts child categories checked: {summary['parts_checked']}
- Parts child categories still needing account correction: {summary['parts_drift']}
- Unreconciled bank statement lines: {summary['unreconciled_lines']}
- Reconciliation queue groups: {summary['queue_groups']}
- Overdue invoices: {summary['overdue_count']}, residual total `{format_money(summary['overdue_total'])}`
- Sales orders ready to invoice: {summary['to_invoice_count']}, order total `{format_money(summary['to_invoice_total'])}`
- Saleable products missing sales tax: {summary['missing_tax_count']}

## Reconciliation Buckets

{bucket_text}

## Working Files

- Category verification: `odoo_imports/product_master/review_reports/operational_details/accounting_parts_category_accounts.csv`
- Category dry-run audit: `odoo_imports/accounting/parts_category_account_fix_dry_run_results.csv`
- Category applied audit: `odoo_imports/accounting/parts_category_account_fix_applied_results.csv`
- Reconciliation summary: `{summary['recon_summary_path'].relative_to(ROOT).as_posix()}`
- Reconciliation line detail: `{summary['recon_lines_path'].relative_to(ROOT).as_posix()}`
- Overdue invoices: `odoo_imports/product_master/review_reports/operational_details/accounting_overdue_documents.csv`
- Sales orders to invoice: `odoo_imports/product_master/review_reports/operational_details/sales_orders_to_invoice.csv`
- Missing sales tax products: `odoo_imports/product_master/review_reports/operational_details/accounting_sale_products_missing_tax.csv`

## Human Decisions

- Confirm whether POS Down Payment, Gift Card, and Top-up eWallet should remain untaxed or receive sales-tax defaults.
- Match customer/card deposits to Shop Boss, POS, and card batches before coding.
- Use check register or statement images for checks that still lack payee detail.
- Match credit-card, loan, and tax payments to liability accounts before reconciling.
""",
        encoding="utf-8",
    )


def build_summary(applied, month):
    recon_lines_path, recon_summary_path = dated_recon_paths(month)
    recon_lines = read_csv(recon_lines_path)
    recon_summary = read_csv(recon_summary_path)
    overdue = [row for row in read_csv(OVERDUE_PATH) if in_month(row.get("invoice_date_due"), month, date_only=True)]
    to_invoice = [row for row in read_csv(TO_INVOICE_PATH) if in_month(row.get("date_order"), month)]
    missing_tax = read_csv(MISSING_TAX_PATH)
    parts = read_csv(PARTS_VERIFY_PATH)
    summary = {
        "applied": applied,
        "month": month,
        "period": month_label(month) if month else "All Dates",
        "recon_lines_path": recon_lines_path,
        "recon_summary_path": recon_summary_path,
        "parts_checked": sum(1 for row in parts if (row.get("complete_name") or "").startswith("Parts /")),
        "parts_drift": parts_drift_count(parts),
        "unreconciled_lines": len(recon_lines),
        "queue_groups": len(recon_summary),
        "overdue_count": len(overdue),
        "overdue_total": sum_field(overdue, "amount_residual"),
        "to_invoice_count": len(to_invoice),
        "to_invoice_total": sum_field(to_invoice, "amount_total"),
        "missing_tax_count": len(missing_tax),
        "buckets": bucket_totals(recon_summary),
    }
    return summary


def print_summary(summary):
    print("\n== Accounting Agent Summary ==")
    print(f"Focus: {summary['period']}")
    print(f"Odoo write performed: {'yes, category fixes applied' if summary['applied'] else 'no'}")
    print(f"Parts child categories checked: {summary['parts_checked']}")
    print(f"Parts child categories still needing correction: {summary['parts_drift']}")
    print(f"Unreconciled bank statement lines: {summary['unreconciled_lines']}")
    print(f"Reconciliation queue groups: {summary['queue_groups']}")
    print(f"Overdue invoices: {summary['overdue_count']} ({format_money(summary['overdue_total'])})")
    print(f"Sales orders ready to invoice: {summary['to_invoice_count']} ({format_money(summary['to_invoice_total'])})")
    print(f"Saleable products missing sales tax: {summary['missing_tax_count']}")
    print(f"Report: {CHECKLIST_PATH}")


def main():
    parser = argparse.ArgumentParser(description="Run the local Odoo Accounting agent workflow.")
    parser.add_argument("--month", default=DEFAULT_MONTH, help="Focus on one month in YYYY-MM format. Default: 2026-07.")
    parser.add_argument("--all-dates", action="store_true", help="Use all dates instead of the default July 2026 focus.")
    parser.add_argument(
        "--apply-category-fixes",
        action="store_true",
        help="Apply the Parts child category account correction. Default is read-only.",
    )
    args = parser.parse_args()
    month = None if args.all_dates else args.month

    category_cmd = [sys.executable, "scripts/odoo_fix_parts_category_accounts.py"]
    if args.apply_category_fixes:
        category_cmd.append("--apply")

    run_step(category_cmd, "Category accounting check")
    recon_cmd = [sys.executable, "scripts/odoo_accounting_reconciliation_queue.py"]
    if month:
        recon_cmd.extend(["--month", month])
    run_step(recon_cmd, "Live reconciliation queue")
    run_step([sys.executable, "scripts/odoo_operational_audit_details.py"], "Operational detail reports")
    run_step([sys.executable, "scripts/odoo_operational_audit.py"], "Operational audit summary")

    summary = build_summary(args.apply_category_fixes, month)
    write_checklist(summary)
    print_summary(summary)


if __name__ == "__main__":
    main()
