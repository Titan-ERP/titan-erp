import csv
import json
import re
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHOP_JSON = ROOT / "odoo_imports" / "shop_boss" / "shop_boss_production_detail_with_payments_2026_07.json"
ODOO_INVOICES = ROOT / "odoo_imports" / "accounting" / "odoo_july_invoice_payment_export.csv"
OUT_DIR = ROOT / "odoo_imports" / "shop_boss"
SHOP_PART_SALES = OUT_DIR / "shop_boss_part_sales_production_detail_2026_07.csv"
MATCH_AUDIT = OUT_DIR / "shop_boss_odoo_existing_invoice_match_audit_2026_07.csv"
UNMATCHED_ODOO = OUT_DIR / "odoo_july_invoices_without_shop_boss_part_sale_match_2026_07.csv"
SUMMARY = OUT_DIR / "shop_boss_odoo_existing_match_summary_2026_07.md"

CREATED_INITIAL_FIX_REFS = {"Shop Boss PS 388", "Shop Boss PS 389", "Shop Boss PS 394", "Shop Boss PS 398"}
CREATED_TRUTH_CORRECTION_REFS = {
    "Shop Boss PS 384",
    "Shop Boss PS 385",
    "Shop Boss PS 387",
    "Shop Boss PS 390",
    "Shop Boss PS 391",
    "Shop Boss PS 392",
    "Shop Boss PS 395",
    "Shop Boss PS 396",
    "Shop Boss PS 397",
    "Shop Boss PS 399",
    "Shop Boss PS 404",
    "Shop Boss PS 405",
    "Shop Boss PS 406",
    "Shop Boss PS 407",
    "Shop Boss PS 408",
    "Shop Boss PS 409",
}


def money(value):
    text = str(value or "0").replace("$", "").replace(",", "").strip()
    return Decimal(text or "0").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def cents(value):
    return int(money(value) * 100)


def iso_from_mmddyyyy(value):
    month, day, year = str(value).split("/")
    return f"{year}-{month.zfill(2)}-{day.zfill(2)}"


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def norm(value):
    raw = str(value or "").upper().replace("&", " AND ").strip()
    if "," in raw:
        left, right = [part.strip() for part in raw.split(",", 1)]
        if left and right and len(right.split()) <= 3:
            raw = f"{right} {left}"
    text = raw
    text = text.replace(",", " ")
    text = re.sub(r"[^A-Z0-9]+", " ", text)
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
        oy, om, od = [int(part) for part in odoo_iso.split("-")]
        return abs((date(sy, sm, sd) - date(oy, om, od)).days)
    except Exception:
        return 999


def parse_shop_part_sales():
    data = json.loads(SHOP_JSON.read_text(encoding="utf-8"))
    rows = []
    for table in data.get("tables", []):
        for cells in table.get("rows", []):
            if len(cells) < 14 or not re.fullmatch(r"\d+", str(cells[0] or "")):
                continue
            total = money(cells[12])
            if total == 0:
                continue
            rows.append(
                {
                    "Shop Boss PS": cells[0],
                    "Closed Date": cells[1],
                    "Closed Date ISO": iso_from_mmddyyyy(cells[1]),
                    "Shop Boss Customer": cells[2].strip().rstrip(","),
                    "Parts": money(cells[4]),
                    "Fees": money(cells[6]),
                    "Tax": money(cells[7]),
                    "Payments": money(cells[10]),
                    "Payment Source": cells[11].strip(),
                    "Total Sale": total,
                    "Parts Cost": money(cells[13]),
                }
            )
    return rows


def candidate_score(shop, invoice):
    score = 0
    reasons = []
    if cents(shop["Total Sale"]) == cents(invoice.get("amount_total")):
        score += 100
        reasons.append("exact total")
    elif abs(cents(shop["Total Sale"]) - cents(invoice.get("amount_total"))) <= 2:
        score += 80
        reasons.append("near total")

    cscore = customer_score(shop["Shop Boss Customer"], invoice.get("partner_id"))
    if cscore:
        score += cscore
        reasons.append(f"customer score {cscore}")

    if cents(shop["Tax"]) == cents(invoice.get("amount_tax")):
        score += 20
        reasons.append("tax matches")

    if cents(shop["Parts"] + shop["Fees"]) == cents(invoice.get("amount_untaxed")):
        score += 20
        reasons.append("untaxed matches")

    days = date_distance(shop["Closed Date ISO"], invoice.get("invoice_date"))
    if days == 0:
        score += 20
        reasons.append("same date")
    elif days <= 7:
        score += 10
        reasons.append(f"date within {days} days")

    ref = str(invoice.get("ref") or "")
    origin = str(invoice.get("invoice_origin") or "")
    if shop["Shop Boss PS"] and shop["Shop Boss PS"] in f"{ref} {origin}":
        score += 120
        reasons.append("Shop Boss PS in Odoo ref/origin")

    return score, "; ".join(reasons)


def is_candidate(shop, invoice, score, reasons):
    if "exact total" in reasons or "near total" in reasons or "Shop Boss PS" in reasons:
        return True
    if customer_score(shop["Shop Boss Customer"], invoice.get("partner_id")) >= 60:
        return date_distance(shop["Closed Date ISO"], invoice.get("invoice_date")) <= 3
    return False


def classify_match(score, candidate_count, invoice):
    if score >= 220:
        return "Confirmed"
    if score >= 170 and candidate_count == 1:
        return "Likely"
    if score >= 120:
        return "Review"
    if score >= 100:
        return "Amount-only review"
    return "No Odoo match"


def classify_source(invoice):
    ref_text = f"{invoice.get('ref') or ''} {invoice.get('invoice_origin') or ''}"
    if any(ref in ref_text for ref in CREATED_INITIAL_FIX_REFS):
        return "Created initial fix"
    if any(ref in ref_text for ref in CREATED_TRUTH_CORRECTION_REFS):
        return "Created truth correction"
    return "Pre-existing Odoo"


def main():
    shop_rows = parse_shop_part_sales()
    invoices = read_csv(ODOO_INVOICES)
    used_invoice_ids = set()
    audit_rows = []

    for shop in shop_rows:
        candidates = []
        for invoice in invoices:
            score, reasons = candidate_score(shop, invoice)
            if score >= 100 and is_candidate(shop, invoice, score, reasons):
                candidates.append((score, reasons, invoice))
        candidates.sort(key=lambda item: item[0], reverse=True)
        best_score, best_reasons, best = (0, "", {})
        if candidates:
            best_score, best_reasons, best = candidates[0]
            used_invoice_ids.add(best.get("id", ""))
        status = classify_match(best_score, len(candidates), best)
        source = classify_source(best) if best else ""
        if not best:
            source = ""
        audit_rows.append(
            {
                "Status": status,
                "Odoo Source": source,
                "Confidence": best_score,
                "Reasons": best_reasons,
                "Candidate Count": len(candidates),
                "Shop Boss PS": shop["Shop Boss PS"],
                "Shop Boss Closed Date": shop["Closed Date ISO"],
                "Shop Boss Customer": shop["Shop Boss Customer"],
                "Shop Boss Parts": shop["Parts"],
                "Shop Boss Tax": shop["Tax"],
                "Shop Boss Total": shop["Total Sale"],
                "Shop Boss Payments": shop["Payments"],
                "Shop Boss Payment Source": shop["Payment Source"],
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

    unmatched_odoo = []
    for invoice in invoices:
        if invoice.get("id") in used_invoice_ids:
            continue
        if invoice.get("ref") in CREATED_INITIAL_FIX_REFS | CREATED_TRUTH_CORRECTION_REFS:
            continue
        unmatched_odoo.append(invoice)

    shop_fields = [
        "Shop Boss PS",
        "Closed Date",
        "Closed Date ISO",
        "Shop Boss Customer",
        "Parts",
        "Fees",
        "Tax",
        "Payments",
        "Payment Source",
        "Total Sale",
        "Parts Cost",
    ]
    audit_fields = [
        "Status",
        "Odoo Source",
        "Confidence",
        "Reasons",
        "Candidate Count",
        "Shop Boss PS",
        "Shop Boss Closed Date",
        "Shop Boss Customer",
        "Shop Boss Parts",
        "Shop Boss Tax",
        "Shop Boss Total",
        "Shop Boss Payments",
        "Shop Boss Payment Source",
        "Odoo Invoice ID",
        "Odoo Invoice",
        "Odoo State",
        "Odoo Payment State",
        "Odoo Customer",
        "Odoo Date",
        "Odoo Untaxed",
        "Odoo Tax",
        "Odoo Total",
        "Odoo Residual",
        "Odoo Origin",
        "Odoo Ref",
    ]
    write_csv(SHOP_PART_SALES, shop_rows, shop_fields)
    write_csv(MATCH_AUDIT, audit_rows, audit_fields)
    write_csv(UNMATCHED_ODOO, unmatched_odoo, list(invoices[0].keys()) if invoices else [])

    counts = Counter(row["Status"] for row in audit_rows)
    source_counts = Counter(row["Odoo Source"] for row in audit_rows if row["Odoo Source"])
    confirmed_preexisting = [
        row for row in audit_rows
        if row["Status"] == "Confirmed" and row["Odoo Source"] == "Pre-existing Odoo"
    ]
    review_rows = [row for row in audit_rows if row["Status"] in {"Review", "Amount-only review", "No Odoo match"}]

    SUMMARY.write_text(
        "\n".join(
            [
                "# Shop Boss / Odoo Existing Invoice Match Audit",
                "",
                "Scope: July 2026 Shop Boss part sales vs Odoo July customer invoices.",
                "",
                "## Results",
                "",
                f"- Shop Boss July part-sale rows reviewed: {len(shop_rows)}",
                f"- Confirmed matches: {counts.get('Confirmed', 0)}",
                f"- Likely matches: {counts.get('Likely', 0)}",
                f"- Review matches: {counts.get('Review', 0)}",
                f"- Amount-only review matches: {counts.get('Amount-only review', 0)}",
                f"- No Odoo match: {counts.get('No Odoo match', 0)}",
                f"- Confirmed pre-existing Odoo matches: {len(confirmed_preexisting)}",
                f"- Confirmed initial-fix matches: {source_counts.get('Created initial fix', 0)}",
                f"- Confirmed truth-correction matches: {source_counts.get('Created truth correction', 0)}",
                f"- Odoo July invoices without a Shop Boss part-sale match: {len(unmatched_odoo)}",
                "",
                "## Files",
                "",
                f"- Match audit: `{MATCH_AUDIT.relative_to(ROOT).as_posix()}`",
                f"- Parsed Shop Boss part sales: `{SHOP_PART_SALES.relative_to(ROOT).as_posix()}`",
                f"- Odoo invoices without Shop Boss part-sale match: `{UNMATCHED_ODOO.relative_to(ROOT).as_posix()}`",
                "",
                "## Notes",
                "",
                "- This audit checks Shop Boss part sales only. Odoo invoices without a part-sale match may be repair orders, rentals, manual invoices, or non-Shop-Boss activity.",
                "- Confirmed means total, customer, tax/untaxed values, date, or explicit Shop Boss reference provided enough evidence.",
                "- Review rows should not be auto-changed without invoice-level support.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    print(f"Shop Boss part-sale rows: {len(shop_rows)}")
    print(f"Match counts: {dict(counts)}")
    print(f"Source counts: {dict(source_counts)}")
    print(f"Unmatched Odoo invoices: {len(unmatched_odoo)}")
    print(f"Summary: {SUMMARY}")


if __name__ == "__main__":
    main()
