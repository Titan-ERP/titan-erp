import csv
import json
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHOP_JSON = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_finalized_closed_production_detail_with_payments_2026_07.json"
ODOO_INVOICES = ROOT / "odoo_imports" / "accounting" / "odoo_july_invoice_payment_export.csv"
OUT_DIR = ROOT / "odoo_imports" / "shop_boss"
SHOP_ALL = OUT_DIR / "shop_boss_all_invoice_rows_finalized_closed_2026_07.csv"
MATCH_AUDIT = OUT_DIR / "shop_boss_odoo_all_invoice_match_audit_2026_07.csv"
ODOO_COVERAGE = OUT_DIR / "odoo_all_july_invoice_shop_boss_coverage_audit_2026_07.csv"
SUMMARY = OUT_DIR / "shop_boss_odoo_all_invoice_match_summary_2026_07.md"


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cents(value):
    return int(money(value) * 100)


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def iso_from_mmddyyyy(value):
    if not value:
        return ""
    month, day, year = str(value).split("/")
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def clean_text(value):
    text = str(value or "").replace("\b", "").replace("\x01", "").replace("\x02", "").replace("\x03", "")
    return re.sub(r"\s+", " ", text).strip().rstrip(",")


def norm(value):
    raw = clean_text(value).upper().replace("&", " AND ")
    if "," in raw:
        left, right = [part.strip() for part in raw.split(",", 1)]
        if left and right and len(right.split()) <= 3:
            raw = f"{right} {left}"
    text = re.sub(r"[^A-Z0-9]+", " ", raw)
    return re.sub(r"\s+", " ", text).strip()


def customer_tokens(value):
    stop = {"INC", "LLC", "CO", "COMPANY", "THE", "AND", "DBA", "ACCOUNTS", "PAYABLE"}
    return {token for token in norm(value).split() if len(token) > 1 and token not in stop}


def customer_score(shop_customer, odoo_customer):
    shop_tokens = customer_tokens(shop_customer)
    odoo_tokens = customer_tokens(odoo_customer)
    if not shop_tokens or not odoo_tokens:
        return 0
    if norm(shop_customer) == norm(odoo_customer):
        return 80
    overlap = shop_tokens & odoo_tokens
    if not overlap:
        return 0
    return min(70, int(100 * len(overlap) / max(len(shop_tokens), len(odoo_tokens))))


def date_distance(shop_iso, odoo_iso):
    try:
        from datetime import date

        sy, sm, sd = [int(part) for part in shop_iso.split("-")]
        oy, om, od = [int(part) for part in str(odoo_iso).split("-")]
        return abs((date(sy, sm, sd) - date(oy, om, od)).days)
    except Exception:
        return 999


def parse_shop_rows():
    data = json.loads(SHOP_JSON.read_text(encoding="utf-8"))
    rows = []
    for table in data.get("tables", []):
        table_rows = table.get("rows", [])
        if not table_rows:
            continue
        header = table_rows[0]
        if header and header[0] == "RO#":
            for cells in table_rows[1:]:
                if len(cells) < 14 or not re.fullmatch(r"\d+", str(cells[0] or "")):
                    continue
                total = money(cells[12])
                if total == 0:
                    continue
                rows.append(
                    {
                        "Shop Boss Type": "RO",
                        "Shop Boss Number": cells[0],
                        "Shop Boss Date": cells[1],
                        "Shop Boss Date ISO": iso_from_mmddyyyy(cells[1]),
                        "Shop Boss Customer": clean_text(cells[2]),
                        "Labor": money(cells[3]),
                        "Parts": money(cells[4]),
                        "Sublet": money(cells[5]),
                        "Fees": money(cells[6]),
                        "Tax": money(cells[7]),
                        "Discount": money(cells[8]),
                        "Payments": money(cells[10]),
                        "Payment Source": clean_text(cells[11]),
                        "Total": total,
                        "Parts Cost": money(cells[13]),
                    }
                )
        elif header and header[0] == "PS #":
            for cells in table_rows[1:]:
                if len(cells) < 14 or not re.fullmatch(r"\d+", str(cells[0] or "")):
                    continue
                total = money(cells[12])
                if total == 0:
                    continue
                rows.append(
                    {
                        "Shop Boss Type": "PS",
                        "Shop Boss Number": cells[0],
                        "Shop Boss Date": cells[1],
                        "Shop Boss Date ISO": iso_from_mmddyyyy(cells[1]),
                        "Shop Boss Customer": clean_text(cells[2]),
                        "Labor": money("0"),
                        "Parts": money(cells[4]),
                        "Sublet": money("0"),
                        "Fees": money(cells[6]),
                        "Tax": money(cells[7]),
                        "Discount": money("0"),
                        "Payments": money(cells[10]),
                        "Payment Source": clean_text(cells[11]),
                        "Total": total,
                        "Parts Cost": money(cells[13]),
                    }
                )
    return rows


def source_ref(shop):
    return f"Shop Boss {shop['Shop Boss Type']} {shop['Shop Boss Number']}"


def candidate_score(shop, invoice):
    score = 0
    reasons = []
    if cents(shop["Total"]) == cents(invoice.get("amount_total")):
        score += 100
        reasons.append("exact total")
    elif abs(cents(shop["Total"]) - cents(invoice.get("amount_total"))) <= 2:
        score += 80
        reasons.append("near total")

    cscore = customer_score(shop["Shop Boss Customer"], invoice.get("partner_id"))
    if cscore:
        score += cscore
        reasons.append(f"customer score {cscore}")

    if cents(shop["Tax"]) == cents(invoice.get("amount_tax")):
        score += 20
        reasons.append("tax matches")

    days = date_distance(shop["Shop Boss Date ISO"], invoice.get("invoice_date"))
    if days == 0:
        score += 20
        reasons.append("same date")
    elif days <= 7:
        score += 10
        reasons.append(f"date within {days} days")

    ref_text = f"{invoice.get('ref') or ''} {invoice.get('invoice_origin') or ''}"
    if source_ref(shop) in ref_text or shop["Shop Boss Number"] in ref_text:
        score += 120
        reasons.append("Shop Boss number in Odoo ref/origin")

    return score, "; ".join(reasons)


def is_candidate(shop, invoice, reasons):
    if "Shop Boss number" in reasons or "exact total" in reasons or "near total" in reasons:
        return True
    if customer_score(shop["Shop Boss Customer"], invoice.get("partner_id")) >= 60:
        return date_distance(shop["Shop Boss Date ISO"], invoice.get("invoice_date")) <= 7
    return False


def classify(score, candidate_count):
    if score >= 220:
        return "Confirmed"
    if score >= 170 and candidate_count == 1:
        return "Likely"
    if score >= 120:
        return "Review"
    return "No Odoo match"


def main():
    shop_rows = parse_shop_rows()
    invoices = [
        row for row in read_csv(ODOO_INVOICES)
        if not row.get("move_type") or row.get("move_type") == "out_invoice"
    ]
    used_invoice_ids = set()
    audit_rows = []

    for shop in shop_rows:
        candidates = []
        for invoice in invoices:
            score, reasons = candidate_score(shop, invoice)
            if score >= 100 and is_candidate(shop, invoice, reasons):
                candidates.append((score, reasons, invoice))
        candidates.sort(key=lambda item: item[0], reverse=True)
        if candidates:
            score, reasons, best = candidates[0]
            used_invoice_ids.add(best.get("id", ""))
        else:
            score, reasons, best = 0, "", {}
        audit_rows.append(
            {
                "Status": classify(score, len(candidates)),
                "Confidence": score,
                "Reasons": reasons,
                "Candidate Count": len(candidates),
                **shop,
                "Odoo Invoice ID": best.get("id", ""),
                "Odoo Invoice": best.get("name", ""),
                "Odoo State": best.get("state", ""),
                "Odoo Payment State": best.get("payment_state", ""),
                "Odoo Customer": best.get("partner_id", ""),
                "Odoo Date": best.get("invoice_date", ""),
                "Odoo Untaxed": best.get("amount_untaxed", ""),
                "Odoo Tax": best.get("amount_tax", ""),
                "Odoo Total": best.get("amount_total", ""),
                "Odoo Residual": best.get("amount_residual", ""),
                "Odoo Origin": best.get("invoice_origin", ""),
                "Odoo Ref": best.get("ref", ""),
            }
        )

    coverage_rows = []
    by_id = {row["Odoo Invoice ID"]: row for row in audit_rows if row["Odoo Invoice ID"]}
    for invoice in invoices:
        matched = by_id.get(invoice["id"])
        if matched:
            status = f"Matched Shop Boss {matched['Shop Boss Type']}"
            issue = ""
        else:
            status = "No Shop Boss match in finalized/closed report"
            issue = "Could be rental/manual/non-Shop-Boss activity, but it is not supported by the July Shop Boss finalized/closed production report."
        coverage_rows.append(
            {
                "Coverage Status": status,
                "Issue": issue,
                "Odoo Invoice ID": invoice.get("id", ""),
                "Odoo Invoice": invoice.get("name", ""),
                "Odoo Date": invoice.get("invoice_date", ""),
                "Odoo Customer": invoice.get("partner_id", ""),
                "Odoo State": invoice.get("state", ""),
                "Odoo Untaxed": invoice.get("amount_untaxed", ""),
                "Odoo Tax": invoice.get("amount_tax", ""),
                "Odoo Total": invoice.get("amount_total", ""),
                "Odoo Payment State": invoice.get("payment_state", ""),
                "Odoo Residual": invoice.get("amount_residual", ""),
                "Odoo Origin": invoice.get("invoice_origin", ""),
                "Odoo Ref": invoice.get("ref", ""),
                "Odoo Reversal IDs": invoice.get("reversal_move_ids", ""),
                "Shop Boss Type": matched.get("Shop Boss Type", "") if matched else "",
                "Shop Boss Number": matched.get("Shop Boss Number", "") if matched else "",
                "Shop Boss Customer": matched.get("Shop Boss Customer", "") if matched else "",
                "Shop Boss Total": matched.get("Total", "") if matched else "",
            }
        )

    shop_fields = [
        "Shop Boss Type", "Shop Boss Number", "Shop Boss Date", "Shop Boss Date ISO", "Shop Boss Customer",
        "Labor", "Parts", "Sublet", "Fees", "Tax", "Discount", "Payments", "Payment Source", "Total", "Parts Cost",
    ]
    audit_fields = [
        "Status", "Confidence", "Reasons", "Candidate Count", *shop_fields,
        "Odoo Invoice ID", "Odoo Invoice", "Odoo State", "Odoo Payment State", "Odoo Customer", "Odoo Date",
        "Odoo Untaxed", "Odoo Tax", "Odoo Total", "Odoo Residual", "Odoo Origin", "Odoo Ref",
    ]
    coverage_fields = list(coverage_rows[0].keys()) if coverage_rows else []
    write_csv(SHOP_ALL, shop_rows, shop_fields)
    write_csv(MATCH_AUDIT, audit_rows, audit_fields)
    write_csv(ODOO_COVERAGE, coverage_rows, coverage_fields)

    counts = Counter(row["Status"] for row in audit_rows)
    coverage_counts = Counter(row["Coverage Status"] for row in coverage_rows)
    unmatched_shop = [row for row in audit_rows if row["Status"] == "No Odoo match"]
    review_shop = [row for row in audit_rows if row["Status"] in {"Likely", "Review"}]
    unmatched_odoo = [row for row in coverage_rows if row["Coverage Status"] == "No Shop Boss match in finalized/closed report"]
    unaddressed_odoo = [
        row for row in unmatched_odoo
        if row.get("Odoo State") != "cancel"
        and row.get("Odoo Payment State") != "reversed"
        and not row.get("Odoo Reversal IDs")
    ]
    shop_total = sum((money(row["Total"]) for row in shop_rows), Decimal("0.00"))
    matched_total = sum((money(row["Total"]) for row in audit_rows if row["Status"] == "Confirmed"), Decimal("0.00"))

    lines = [
        "# Shop Boss / Odoo All Invoice Match Audit",
        "",
        "Scope: July 2026 Shop Boss finalized+closed production detail, including repair orders and part sales, against live Odoo July customer invoices.",
        "",
        "## Results",
        "",
        f"- Shop Boss invoice rows reviewed: {len(shop_rows)}",
        f"- Shop Boss report total: `${shop_total:,.2f}`",
        f"- Confirmed Odoo matches: {counts.get('Confirmed', 0)}",
        f"- Confirmed matched total: `${matched_total:,.2f}`",
        f"- Likely/review rows: {len(review_shop)}",
        f"- Shop Boss rows with no Odoo match: {len(unmatched_shop)}",
        f"- Odoo July invoices not matched to this Shop Boss report: {len(unmatched_odoo)}",
        f"- Odoo-only invoices still unaddressed by cancel/reversal: {len(unaddressed_odoo)}",
        "",
        "## Counts",
        "",
    ]
    for key, value in counts.items():
        lines.append(f"- Shop Boss {key}: {value}")
    for key, value in coverage_counts.items():
        lines.append(f"- Odoo coverage {key}: {value}")
    lines += [
        "",
        "## Files",
        "",
        f"- Shop Boss parsed rows: `{SHOP_ALL.relative_to(ROOT).as_posix()}`",
        f"- Match audit: `{MATCH_AUDIT.relative_to(ROOT).as_posix()}`",
        f"- Odoo coverage audit: `{ODOO_COVERAGE.relative_to(ROOT).as_posix()}`",
    ]
    SUMMARY.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Shop Boss rows: {len(shop_rows)}")
    print(f"Shop Boss counts: {dict(counts)}")
    print(f"Odoo coverage counts: {dict(coverage_counts)}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    main()
