import csv
import os
import re
import xmlrpc.client
from collections import defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
OUT = ROOT / "odoo_imports" / "bank_reconciliation"
CANDIDATES = OUT / "odoo_bank_reconciliation_candidates.csv"


def load_env(path):
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env(ENV_PATH)
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
    uid = common.authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")
    return db, uid, api_key, models


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read(models, db, uid, api_key, model, domain, fields, limit=10000, order=None, context=None):
    kwargs = {"fields": fields, "limit": limit}
    if order:
        kwargs["order"] = order
    if context:
        kwargs["context"] = context
    return execute(models, db, uid, api_key, model, "search_read", [domain], kwargs)


def rel(value):
    if isinstance(value, list) and len(value) >= 2:
        return value[1]
    return ""


def cents(value):
    return int((Decimal(str(value or "0")).quantize(Decimal("0.01"))) * 100)


def norm(text):
    return re.sub(r"[^a-z0-9]+", " ", str(text or "").lower()).strip()


def tokens(text):
    return {part for part in norm(text).split() if len(part) >= 3}


def write_csv(rows):
    OUT.mkdir(parents=True, exist_ok=True)
    fields = [
        "Confidence",
        "Reason",
        "Bank Statement Line ID",
        "Bank Date",
        "Bank Ref",
        "Bank Partner",
        "Bank Amount",
        "Bank Move",
        "Candidate Move Line ID",
        "Candidate Date",
        "Candidate Name",
        "Candidate Ref",
        "Candidate Partner",
        "Candidate Account",
        "Candidate Journal",
        "Candidate Residual",
        "Candidate Balance",
    ]
    with CANDIDATES.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main():
    db, uid, api_key, models = connect()
    bank_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.bank.statement.line",
        [("date", ">=", "2026-03-01"), ("date", "<=", "2026-06-30"), ("is_reconciled", "=", False)],
        ["id", "date", "payment_ref", "partner_id", "amount", "journal_id", "move_id"],
        order="date asc",
    )

    open_lines = read(
        models,
        db,
        uid,
        api_key,
        "account.move.line",
        [
            ("date", ">=", "2026-01-01"),
            ("date", "<=", "2026-07-31"),
            ("reconciled", "=", False),
            ("account_id.reconcile", "=", True),
            ("parent_state", "=", "posted"),
        ],
        [
            "id",
            "date",
            "name",
            "ref",
            "partner_id",
            "account_id",
            "journal_id",
            "move_id",
            "balance",
            "amount_residual",
        ],
        limit=20000,
        order="date asc",
    )

    by_abs_residual = defaultdict(list)
    for line in open_lines:
        residual = line.get("amount_residual")
        if residual in (None, False):
            residual = line.get("balance")
        by_abs_residual[abs(cents(residual))].append(line)

    candidates = []
    used_statement_lines = set()
    for bank in bank_lines:
        amount_cents = abs(cents(bank["amount"]))
        possible = by_abs_residual.get(amount_cents, [])
        if not possible:
            continue

        bank_partner = rel(bank.get("partner_id"))
        bank_ref = bank.get("payment_ref") or ""
        bank_text = f"{bank_partner} {bank_ref}"
        bank_tokens = tokens(bank_text)
        ranked = []
        for line in possible:
            cand_partner = rel(line.get("partner_id"))
            cand_text = f"{cand_partner} {line.get('name')} {line.get('ref')} {rel(line.get('move_id'))}"
            shared = bank_tokens & tokens(cand_text)
            score = 0
            reasons = []
            if bank_partner and cand_partner and norm(bank_partner) == norm(cand_partner):
                score += 80
                reasons.append("same partner")
            if shared:
                score += min(20, len(shared) * 5)
                reasons.append("shared text: " + " ".join(sorted(shared)[:5]))
            if line.get("date") and bank.get("date") and str(line["date"]) <= str(bank["date"]):
                score += 5
                reasons.append("candidate date before/on bank date")
            if amount_cents:
                score += 40
                reasons.append("exact amount")

            # Avoid ambiguous exact-amount-only matches.
            if score >= 120 or (score >= 100 and len(possible) <= 2):
                ranked.append((score, "; ".join(reasons), line))

        ranked.sort(key=lambda item: item[0], reverse=True)
        if len(ranked) == 1 or (len(ranked) > 1 and ranked[0][0] > ranked[1][0] + 20):
            score, reason, line = ranked[0]
            used_statement_lines.add(bank["id"])
            candidates.append(
                {
                    "Confidence": score,
                    "Reason": reason,
                    "Bank Statement Line ID": bank["id"],
                    "Bank Date": bank.get("date", ""),
                    "Bank Ref": bank_ref,
                    "Bank Partner": bank_partner,
                    "Bank Amount": bank.get("amount", ""),
                    "Bank Move": rel(bank.get("move_id")),
                    "Candidate Move Line ID": line["id"],
                    "Candidate Date": line.get("date", ""),
                    "Candidate Name": line.get("name", ""),
                    "Candidate Ref": line.get("ref", ""),
                    "Candidate Partner": rel(line.get("partner_id")),
                    "Candidate Account": rel(line.get("account_id")),
                    "Candidate Journal": rel(line.get("journal_id")),
                    "Candidate Residual": line.get("amount_residual", ""),
                    "Candidate Balance": line.get("balance", ""),
                }
            )

    write_csv(candidates)
    print(f"Connected uid: {uid}")
    print(f"Unreconciled bank lines reviewed: {len(bank_lines)}")
    print(f"Open reconcilable move lines reviewed: {len(open_lines)}")
    print(f"High-confidence candidates: {len(candidates)}")
    print(f"Candidate file: {CANDIDATES}")


if __name__ == "__main__":
    main()
