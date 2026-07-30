import argparse
import csv
import os
import re
import xmlrpc.client
from collections import defaultdict
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
SHOP_BOSS_DIR = ROOT / "odoo_imports" / "shop_boss"
OUT_DIR = ROOT / "odoo_imports" / "accounting"
DETAIL_OUT = OUT_DIR / "invoice_revenue_bucket_audit_2026_06_07.csv"
SUMMARY_OUT = OUT_DIR / "invoice_revenue_bucket_audit_2026_06_07.md"

COMPANY = "Southern Equipment Company (Laurel)"
JOURNAL = "Miscellaneous Operations"
DATE_FROM = "2026-06-01"
DATE_TO = "2026-07-31"

ACCOUNTS = {
    "parts": "Parts Revenue",
    "service": "Service Revenue",
    "rental": "Rental Revenue",
    "tax": "Sales Tax Payable",
}

RENTAL_TERMS = ("TX18", "TX10", "TX60", "U35")


def money(value):
    text = str(value or "0").strip().replace("$", "").replace(",", "")
    if text in {"", "-"}:
        text = "0"
    return Decimal(text).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def fmt(value):
    return str(money(value))


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def search_read(models, db, uid, api_key, model, domain, fields, limit=1000, order=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def single(models, db, uid, api_key, model, domain, fields, label):
    rows = search_read(models, db, uid, api_key, model, domain, fields, limit=2)
    if len(rows) != 1:
        raise SystemExit(f"Expected one {label}; found {len(rows)}")
    return rows[0]


def parse_mmddyyyy(value):
    value = (value or "").strip()
    if not value:
        return ""
    return datetime.strptime(value, "%m/%d/%Y").strftime("%Y-%m-%d")


def read_shop_boss():
    ros = {}
    ps = {}
    ro_path = SHOP_BOSS_DIR / "shop_boss_finalized_ro_rows_ytd_2026.csv"
    ps_path = SHOP_BOSS_DIR / "shop_boss_part_sale_rows_ytd_2026.csv"
    with ro_path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            number = str(row.get("ro_number") or "").strip()
            if not number:
                continue
            labor = money(row.get("labor"))
            parts = money(row.get("parts"))
            sublet = money(row.get("sublet"))
            fees = money(row.get("fees"))
            tax = money(row.get("tax"))
            ros[number] = {
                "source": f"Shop Boss RO {number}",
                "date": parse_mmddyyyy(row.get("final_date")),
                "customer": row.get("customer") or "",
                "parts": parts,
                "service": labor + sublet + fees,
                "rental": Decimal("0.00"),
                "tax": tax,
                "total": money(row.get("total_ro")),
                "raw": row.get("raw") or "",
            }
    with ps_path.open("r", newline="", encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            number = str(row.get("ps_number") or "").strip()
            if not number:
                continue
            tax_value = money(row.get("tax"))
            if not tax_value:
                tax_value = money(row.get("source"))
            ps[number] = {
                "source": f"Shop Boss PS {number}",
                "date": parse_mmddyyyy(row.get("closed_date")),
                "customer": row.get("customer") or "",
                "parts": money(row.get("fees")),
                "service": Decimal("0.00"),
                "rental": Decimal("0.00"),
                "tax": tax_value,
                "total": money(row.get("total_sale")),
                "raw": row.get("raw") or "",
            }
    return ros, ps


def extract_refs(text):
    refs = []
    seen = set()
    upper = (text or "").upper()
    for kind, number in re.findall(r"\b(RO|PS)\s*#?\s*(\d{2,6})\b", upper):
        key = (kind, number)
        if key in seen:
            continue
        seen.add(key)
        refs.append(key)
    return refs


def has_rental_signal(text):
    upper = (text or "").upper()
    return any(term in upper for term in RENTAL_TERMS)


def add_bucket(targets, bucket, amount):
    targets[bucket] += money(amount)


def classify_invoice(move, lines, ro_rows, ps_rows):
    evidence_text = " ".join(
        str(value or "")
        for value in [
            move.get("name"),
            move.get("ref"),
            move.get("invoice_origin"),
            move.get("narration"),
            move.get("partner_id")[1] if move.get("partner_id") else "",
            " ".join(str(line.get("name") or "") for line in lines),
            " ".join(str(line.get("product_id")[1] if line.get("product_id") else "") for line in lines),
        ]
    )
    refs = extract_refs(evidence_text)
    targets = defaultdict(lambda: Decimal("0.00"))
    evidence = []
    missing = []
    for kind, number in refs:
        if kind == "RO":
            source = ro_rows.get(number)
        else:
            source = ps_rows.get(number)
        if not source:
            missing.append(f"{kind}{number}")
            continue
        for bucket in ACCOUNTS:
            add_bucket(targets, bucket, source[bucket])
        evidence.append(source["source"])

    if not evidence and has_rental_signal(evidence_text):
        rental_amount = money(move.get("amount_untaxed")) or money(move.get("amount_total"))
        add_bucket(targets, "rental", rental_amount)
        evidence.append("Rental equipment signal: " + ", ".join(term for term in RENTAL_TERMS if term in evidence_text.upper()))

    if evidence:
        status = "classified"
    elif missing:
        status = "review_missing_shop_boss_source"
    else:
        status = "review_unclassified"
    return dict(targets), evidence, missing, status


def account_domain(account_name, company_id):
    return [("name", "=", account_name), ("company_ids", "in", [company_id])]


def current_bucket_totals(lines, account_ids):
    totals = defaultdict(lambda: Decimal("0.00"))
    id_to_bucket = {account_id: bucket for bucket, account_id in account_ids.items()}
    for line in lines:
        account = line.get("account_id")
        if not account:
            continue
        bucket = id_to_bucket.get(account[0])
        if not bucket:
            continue
        credit = money(line.get("credit"))
        debit = money(line.get("debit"))
        totals[bucket] += credit - debit
    return dict(totals)


def build_reclass_lines(row, account_ids):
    line_ids = []
    label = row["evidence"] or row["invoice"]
    for bucket, account_id in account_ids.items():
        delta = money(row[f"target_{bucket}"]) - money(row[f"current_{bucket}"])
        if not delta:
            continue
        if delta > 0:
            line_ids.append((0, 0, {"account_id": account_id, "name": label, "debit": 0.0, "credit": float(delta)}))
        else:
            line_ids.append((0, 0, {"account_id": account_id, "name": label, "debit": float(abs(delta)), "credit": 0.0}))
    return line_ids


def write_detail(rows):
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "status",
        "action",
        "invoice",
        "move_id",
        "invoice_date",
        "partner",
        "payment_state",
        "amount_total",
        "residual",
        "evidence",
        "missing_refs",
        "current_parts",
        "current_service",
        "current_rental",
        "current_tax",
        "target_parts",
        "target_service",
        "target_rental",
        "target_tax",
        "net_delta",
        "reclass_ref",
        "odoo_move",
    ]
    with DETAIL_OUT.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_summary(rows):
    counts = defaultdict(int)
    deltas = defaultdict(lambda: Decimal("0.00"))
    classified_totals = defaultdict(lambda: Decimal("0.00"))
    for row in rows:
        counts[row["action"]] += 1
        for bucket in ACCOUNTS:
            if row["status"] == "classified":
                classified_totals[bucket] += money(row[f"target_{bucket}"])
            if row["action"] in {"ready_reclass", "applied"}:
                deltas[bucket] += money(row[f"target_{bucket}"]) - money(row[f"current_{bucket}"])
    needs = [row for row in rows if row["action"] in {"ready_reclass", "applied"}]
    review = [row for row in rows if row["action"].startswith("review")]
    applied = [row for row in rows if row["action"] == "applied"]
    lines = [
        "# June/July Invoice Revenue Bucket Audit",
        "",
        f"Company: {COMPANY}",
        f"Period: {DATE_FROM} through {DATE_TO}",
        "",
        "## Summary",
        "",
        f"- Invoices/credit notes reviewed: {len(rows)}",
        f"- Already aligned or no action: {counts['aligned'] + counts['cancel_or_reversal_no_action']}",
        f"- Reclass entries applied: {len(applied)}",
        f"- Reclass entries ready: {counts['ready_reclass']}",
        f"- Needs review: {len(review)}",
        "",
        "## Classified Source Totals",
        "",
    ]
    for bucket, account_name in ACCOUNTS.items():
        lines.append(f"- {account_name}: {fmt(classified_totals[bucket])}")
    lines.extend([
        "",
        "## Net Actionable Shift",
        "",
    ]
    )
    for bucket, account_name in ACCOUNTS.items():
        lines.append(f"- {account_name}: {fmt(deltas[bucket])}")
    if needs:
        lines.extend(["", "## Reclass Candidates", ""])
        for row in needs[:30]:
            lines.append(
                f"- {row['invoice']} {row['partner']}: {row['evidence']} "
                f"(parts {row['target_parts']}, service {row['target_service']}, rental {row['target_rental']}, tax {row['target_tax']})"
            )
    if review:
        lines.extend(["", "## Review Queue", ""])
        for row in review[:30]:
            lines.append(f"- {row['invoice']} {row['partner']}: {row['action']} {row['missing_refs']}".rstrip())
    lines.extend(["", f"Detail: `{DETAIL_OUT.relative_to(ROOT).as_posix()}`"])
    SUMMARY_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Audit/apply June-July invoice revenue bucket reclasses from Shop Boss evidence.")
    parser.add_argument("--apply", action="store_true", help="Post reclass journal entries for classified, balanced differences.")
    args = parser.parse_args()

    ro_rows, ps_rows = read_shop_boss()
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

    company = single(models, db, uid, api_key, "res.company", [("name", "=", COMPANY)], ["id", "name"], COMPANY)
    journal = single(models, db, uid, api_key, "account.journal", [("name", "=", JOURNAL), ("company_id", "=", company["id"])], ["id"], JOURNAL)
    accounts = {
        bucket: single(models, db, uid, api_key, "account.account", account_domain(name, company["id"]), ["id", "name"], name)
        for bucket, name in ACCOUNTS.items()
    }
    account_ids = {bucket: account["id"] for bucket, account in accounts.items()}

    moves = search_read(
        models,
        db,
        uid,
        api_key,
        "account.move",
        [
            ("company_id", "=", company["id"]),
            ("move_type", "in", ["out_invoice", "out_refund"]),
            ("invoice_date", ">=", DATE_FROM),
            ("invoice_date", "<=", DATE_TO),
        ],
        [
            "id",
            "name",
            "move_type",
            "state",
            "invoice_date",
            "partner_id",
            "amount_untaxed",
            "amount_tax",
            "amount_total",
            "amount_residual",
            "payment_state",
            "invoice_origin",
            "ref",
            "narration",
            "reversed_entry_id",
            "reversal_move_ids",
        ],
        limit=5000,
        order="invoice_date,id",
    )
    move_ids = [move["id"] for move in moves]
    all_lines = search_read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        [("move_id", "in", move_ids)],
        ["id", "move_id", "name", "product_id", "account_id", "debit", "credit", "display_type"],
        limit=20000,
        order="move_id,id",
    )
    lines_by_move = defaultdict(list)
    for line in all_lines:
        move_id = line["move_id"][0] if isinstance(line.get("move_id"), list) else line.get("move_id")
        lines_by_move[move_id].append(line)

    rows = []
    for move in moves:
        move_lines = lines_by_move[move["id"]]
        current = current_bucket_totals(move_lines, account_ids)
        target, evidence, missing, class_status = classify_invoice(move, move_lines, ro_rows, ps_rows)
        row = {
            "status": class_status,
            "action": "",
            "invoice": move.get("name") or "",
            "move_id": move["id"],
            "invoice_date": move.get("invoice_date") or "",
            "partner": move.get("partner_id")[1] if move.get("partner_id") else "",
            "payment_state": move.get("payment_state") or "",
            "amount_total": fmt(move.get("amount_total")),
            "residual": fmt(move.get("amount_residual")),
            "evidence": "; ".join(evidence),
            "missing_refs": "; ".join(missing),
            "reclass_ref": f"Shop Boss invoice revenue bucket reclass {move.get('name') or move['id']}",
            "odoo_move": "",
        }
        for bucket in ACCOUNTS:
            row[f"current_{bucket}"] = fmt(current.get(bucket))
            row[f"target_{bucket}"] = fmt(target.get(bucket))
        delta_sum = sum((money(row[f"target_{bucket}"]) - money(row[f"current_{bucket}"]) for bucket in ACCOUNTS), Decimal("0.00"))
        row["net_delta"] = fmt(delta_sum)

        has_reversal = bool(move.get("reversed_entry_id") or move.get("reversal_move_ids"))
        if move.get("state") == "cancel" or has_reversal:
            row["action"] = "cancel_or_reversal_no_action"
        elif class_status == "classified":
            any_delta = any(money(row[f"target_{bucket}"]) != money(row[f"current_{bucket}"]) for bucket in ACCOUNTS)
            if not any_delta:
                row["action"] = "aligned"
            elif delta_sum != Decimal("0.00"):
                row["action"] = "review_unbalanced_delta"
            else:
                row["action"] = "ready_reclass"
        else:
            row["action"] = class_status
        rows.append(row)

    if args.apply:
        for row in rows:
            if row["action"] != "ready_reclass":
                continue
            existing = search_read(
                models,
                db,
                uid,
                api_key,
                "account.move",
                [("company_id", "=", company["id"]), ("ref", "=", row["reclass_ref"])],
                ["id", "name", "state"],
                limit=5,
            )
            if existing:
                row["action"] = "applied"
                row["odoo_move"] = existing[0]["name"]
                continue
            line_ids = build_reclass_lines(row, account_ids)
            move_id = execute(
                models,
                db,
                uid,
                api_key,
                "account.move",
                "create",
                [{
                    "company_id": company["id"],
                    "journal_id": journal["id"],
                    "date": row["invoice_date"],
                    "ref": row["reclass_ref"],
                    "line_ids": line_ids,
                }],
            )
            execute(models, db, uid, api_key, "account.move", "action_post", [[move_id]])
            posted = search_read(models, db, uid, api_key, "account.move", [("id", "=", move_id)], ["name"], limit=1)[0]
            row["action"] = "applied"
            row["odoo_move"] = posted["name"]

    write_detail(rows)
    write_summary(rows)
    print(f"Reviewed: {len(rows)}")
    print(f"Ready: {sum(1 for row in rows if row['action'] == 'ready_reclass')}")
    print(f"Applied: {sum(1 for row in rows if row['action'] == 'applied')}")
    print(f"Review: {sum(1 for row in rows if row['action'].startswith('review'))}")
    print(f"Detail: {DETAIL_OUT}")
    print(f"Summary: {SUMMARY_OUT}")


if __name__ == "__main__":
    main()
