import csv
import re
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACCOUNTING = ROOT / "odoo_imports" / "accounting"
SHOP_BOSS = ROOT / "odoo_imports" / "shop_boss"

BANK_DETAIL = ACCOUNTING / "reconciled_bank_matching_audit_2026_detail.csv"
PO_FILE = SHOP_BOSS / "shop_boss_po_open_current_2026.csv"
RO_FILE = SHOP_BOSS / "shop_boss_finalized_ro_rows_ytd_2026.csv"
PS_FILE = SHOP_BOSS / "shop_boss_part_sale_rows_ytd_2026.csv"

DETAIL_OUT = ACCOUNTING / "reconciled_bank_matching_shop_boss_crosscheck_2026_detail.csv"
SUMMARY_OUT = ACCOUNTING / "reconciled_bank_matching_shop_boss_crosscheck_2026_summary.csv"
MD_OUT = ACCOUNTING / "reconciled_bank_matching_shop_boss_crosscheck_2026.md"

STOP_WORDS = {
    "AND",
    "THE",
    "INC",
    "LLC",
    "CO",
    "CORP",
    "COMPANY",
    "POS",
    "PURCHASE",
    "NON",
    "PIN",
    "CHECK",
    "PAYMENT",
    "SERVICE",
    "SERVICES",
    "SOUTHERN",
    "EQUIPMENT",
}


def read_csv(path):
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def money(value):
    text = str(value or "").replace("$", "").replace(",", "").strip()
    if not text:
        return Decimal("0.00")
    try:
        return Decimal(text).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")


def parse_date(value):
    text = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    return None


def normalize(text):
    value = str(text or "").upper()
    value = value.replace("&", " AND ")
    value = re.sub(r"[^A-Z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def tokens(text):
    return [part for part in normalize(text).split() if len(part) >= 2 and part not in STOP_WORDS]


def reverse_comma_name(name):
    text = str(name or "").strip()
    if "," not in text:
        return text
    left, right = [part.strip() for part in text.split(",", 1)]
    return f"{right} {left}".strip()


def token_score(needle, haystack):
    needle_tokens = tokens(needle)
    if not needle_tokens:
        return 0
    hay_tokens = set(tokens(haystack))
    return sum(1 for token in needle_tokens if token in hay_tokens) / len(needle_tokens)


def date_gap(bank_date, evidence_date):
    if not bank_date or not evidence_date:
        return 9999
    return abs((bank_date - evidence_date).days)


def money_close(a, b, tolerance=Decimal("0.02")):
    return abs(abs(money(a)) - abs(money(b))) <= tolerance


def raw_money_values(row):
    values = []
    for value in re.findall(r"\$?-?\d[\d,]*\.\d{2}|\$?-?\d[\d,]+", row.get("raw", "")):
        amount = money(value)
        if amount:
            values.append(amount)
    return values


def build_po_matches(bank, po_rows):
    haystack = " ".join([
        bank.get("Payment Ref", ""),
        bank.get("Bank Partner", ""),
        bank.get("Counterpart Partners", ""),
    ])
    bank_date = parse_date(bank.get("Date"))
    bank_amount = money(bank.get("Amount"))
    matches = []
    for po in po_rows:
        score = token_score(po.get("supplier"), haystack)
        if score < 0.6:
            continue
        gap = date_gap(bank_date, parse_date(po.get("date_issue")))
        amount_match = money_close(bank_amount, po.get("total_po"))
        confidence = "strong" if amount_match and gap <= 45 else "possible"
        matches.append({
            "type": "PO",
            "confidence": confidence,
            "doc": f"PO {po.get('po_number')} {po.get('supplier')} {po.get('status')}",
            "date": po.get("date_issue", ""),
            "amount": po.get("total_po", ""),
            "why": f"supplier token match {score:.0%}; date gap {gap} days; amount {'matches' if amount_match else 'differs'}",
        })
    return sorted(matches, key=lambda row: (row["confidence"] != "strong", row["date"]))[:5]


def build_customer_matches(bank, ro_rows, ps_rows):
    haystack = " ".join([
        bank.get("Payment Ref", ""),
        bank.get("Bank Partner", ""),
        bank.get("Counterpart Partners", ""),
    ])
    bank_date = parse_date(bank.get("Date"))
    bank_amount = money(bank.get("Amount"))
    matches = []
    for ro in ro_rows:
        name = ro.get("customer", "")
        score = max(token_score(name, haystack), token_score(reverse_comma_name(name), haystack))
        if score < 0.6:
            continue
        gap = date_gap(bank_date, parse_date(ro.get("final_date")))
        amounts = [money(ro.get("total_ro")), money(ro.get("first_payment_amount"))] + raw_money_values(ro)
        amount_match = any(abs(abs(bank_amount) - abs(value)) <= Decimal("5.00") for value in amounts if value)
        confidence = "strong" if amount_match and gap <= 60 else "possible"
        matches.append({
            "type": "RO",
            "confidence": confidence,
            "doc": f"RO {ro.get('ro_number')} {name}",
            "date": ro.get("final_date", ""),
            "amount": ro.get("total_ro", ""),
            "why": f"customer token match {score:.0%}; date gap {gap} days; amount {'near' if amount_match else 'not near'}",
        })
    for ps in ps_rows:
        name = ps.get("customer", "")
        score = max(token_score(name, haystack), token_score(reverse_comma_name(name), haystack))
        if score < 0.6:
            continue
        gap = date_gap(bank_date, parse_date(ps.get("closed_date")))
        amounts = [money(ps.get("total_sale")), money(ps.get("pmts"))] + raw_money_values(ps)
        amount_match = any(abs(abs(bank_amount) - abs(value)) <= Decimal("5.00") for value in amounts if value)
        confidence = "strong" if amount_match and gap <= 60 else "possible"
        matches.append({
            "type": "Part Sale",
            "confidence": confidence,
            "doc": f"PS {ps.get('ps_number')} {name}",
            "date": ps.get("closed_date", ""),
            "amount": ps.get("total_sale", ""),
            "why": f"customer token match {score:.0%}; date gap {gap} days; amount {'near' if amount_match else 'not near'}",
        })
    return sorted(matches, key=lambda row: (row["confidence"] != "strong", row["date"]))[:5]


def classify_evidence(bank, po_matches, customer_matches):
    category = bank.get("Category", "")
    accounts = bank.get("Counterpart Accounts", "")
    if po_matches and po_matches[0]["confidence"] == "strong":
        return "Backed by Shop Boss PO", po_matches
    if customer_matches and customer_matches[0]["confidence"] == "strong":
        return "Backed by Shop Boss invoice/RO", customer_matches
    if "deposit" in category.lower() or "Accounts Receivable" in accounts:
        if customer_matches:
            return "Possible Shop Boss invoice/RO evidence", customer_matches
        return "No Shop Boss invoice/RO evidence found", []
    if "Known operating vendor" in category:
        if po_matches:
            return "Possible Shop Boss PO evidence", po_matches
        return "No Shop Boss PO evidence found", []
    if "Check" in category:
        if po_matches:
            return "Possible Shop Boss PO evidence", po_matches
        return "Check not evidenced by current Shop Boss PO list", []
    return "Shop Boss evidence not expected", []


def write_markdown(rows, summary_rows, po_count, ro_count, ps_count):
    counts = Counter(row["Shop Boss Evidence Status"] for row in rows)
    lines = [
        "# Reconciled Bank Matching vs Shop Boss Evidence",
        "",
        "Scope: 2026 reconciled Laurel bank lines compared to current Shop Boss POs and YTD finalized RO/part-sale report rows.",
        "",
        "## Evidence Loaded",
        "",
        f"- Shop Boss current PO rows: {po_count}",
        f"- Shop Boss finalized RO rows: {ro_count}",
        f"- Shop Boss part-sale rows: {ps_count}",
        f"- Odoo reconciled bank lines checked: {len(rows)}",
        "",
        "## Result",
        "",
    ]
    for status, count in counts.most_common():
        lines.append(f"- {status}: {count}")
    lines.extend([
        "",
        "## Priority Notes",
        "",
    ])
    priority = [
        row for row in rows
        if row["Risk"] in {"High", "Medium"} and row["Shop Boss Evidence Status"].startswith("No Shop Boss")
    ][:12]
    if priority:
        for row in priority:
            lines.append(
                f"- {row['Risk']} {row['Date']} {row['Amount']} - {row['Payment Ref']} -> {row['Counterpart Accounts']} ({row['Shop Boss Evidence Status']})"
            )
    else:
        lines.append("- No high/medium rows lacked relevant Shop Boss evidence.")
    lines.extend([
        "",
        "## Files",
        "",
        "- Detail CSV: `odoo_imports/accounting/reconciled_bank_matching_shop_boss_crosscheck_2026_detail.csv`",
        "- Summary CSV: `odoo_imports/accounting/reconciled_bank_matching_shop_boss_crosscheck_2026_summary.csv`",
        "",
        "This crosscheck is read-only and does not change Odoo or Shop Boss.",
    ])
    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    bank_rows = read_csv(BANK_DETAIL)
    po_rows = read_csv(PO_FILE)
    ro_rows = read_csv(RO_FILE)
    ps_rows = read_csv(PS_FILE)

    detail_rows = []
    for bank in bank_rows:
        po_matches = build_po_matches(bank, po_rows)
        customer_matches = build_customer_matches(bank, ro_rows, ps_rows)
        status, used = classify_evidence(bank, po_matches, customer_matches)
        detail_rows.append({
            **bank,
            "Shop Boss Evidence Status": status,
            "Shop Boss Evidence Type": "; ".join(row["type"] for row in used),
            "Shop Boss Evidence Doc": "; ".join(row["doc"] for row in used),
            "Shop Boss Evidence Date": "; ".join(row["date"] for row in used),
            "Shop Boss Evidence Amount": "; ".join(str(row["amount"]) for row in used),
            "Shop Boss Evidence Why": "; ".join(row["why"] for row in used),
        })

    grouped = Counter((row["Risk"], row["Category"], row["Shop Boss Evidence Status"]) for row in detail_rows)
    summary_rows = []
    for (risk, category, status), count in grouped.items():
        net = sum(money(row.get("Amount")) for row in detail_rows if row["Risk"] == risk and row["Category"] == category and row["Shop Boss Evidence Status"] == status)
        summary_rows.append({
            "Risk": risk,
            "Category": category,
            "Shop Boss Evidence Status": status,
            "Count": count,
            "Net Amount": float(net),
        })
    risk_order = {"High": 0, "Medium": 1, "Low": 2, "OK": 3}
    summary_rows.sort(key=lambda row: (risk_order.get(row["Risk"], 9), row["Category"], row["Shop Boss Evidence Status"]))

    fields = list(bank_rows[0].keys()) + [
        "Shop Boss Evidence Status",
        "Shop Boss Evidence Type",
        "Shop Boss Evidence Doc",
        "Shop Boss Evidence Date",
        "Shop Boss Evidence Amount",
        "Shop Boss Evidence Why",
    ]
    write_csv(DETAIL_OUT, detail_rows, fields)
    write_csv(SUMMARY_OUT, summary_rows, ["Risk", "Category", "Shop Boss Evidence Status", "Count", "Net Amount"])
    write_markdown(detail_rows, summary_rows, len(po_rows), len(ro_rows), len(ps_rows))

    print(f"Odoo reconciled bank lines checked: {len(detail_rows)}")
    print(f"Shop Boss PO rows: {len(po_rows)}")
    print(f"Shop Boss RO rows: {len(ro_rows)}")
    print(f"Shop Boss part-sale rows: {len(ps_rows)}")
    print(f"Output: {DETAIL_OUT}")
    print(f"Summary: {SUMMARY_OUT}")


if __name__ == "__main__":
    main()
