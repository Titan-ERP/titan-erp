import csv
import os
import re
import xmlrpc.client
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
ACCOUNTING = ROOT / "odoo_imports" / "accounting"
CROSSCHECK = ACCOUNTING / "reconciled_bank_matching_shop_boss_crosscheck_2026_detail.csv"
OUT = ACCOUNTING / "reconciled_bank_matching_repair_plan_2026.csv"
MD_OUT = ACCOUNTING / "reconciled_bank_matching_repair_plan_2026.md"

SAFE_RECODE_RULES = [
    (r"UPS\*|PAYPAL \*UPS|USPS PO", "Office Expenses", "Shipping/postage bank-card expense miscoded away from office expense."),
    (r"DIXIE ELECTRIC", "Facility Expense", "Utility payment miscoded away from facility expense."),
    (r"JULIA'?SSTEAKHOUSE|SUBWAY|FIREHOUSE SUBS|COCA COLA", "Meals & Entertainment", "Meal/food purchase miscoded away from meals."),
    (r"MACS #|CLARK'?S #49|CIRCLE K|MARATHON|MINIT MART|HAYDEN VALERO", "Company Vehicle Expense", "Fuel/convenience-store purchase miscoded away from vehicle expense."),
    (r"VONAGE BUSINESS|WWW\.SMALINK\.COM|GOOGLE \*WORKSPACE|GOOGLE WORKSPACE|BLS\*SHOP BOSS", "Software Subscriptions", "Recurring software/service coding preference."),
]


def load_env():
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def connect():
    load_env()
    url = os.environ["ODOO_URL"].rstrip("/")
    db = os.environ["ODOO_DB"]
    username = os.environ["ODOO_USERNAME"]
    api_key = os.environ["ODOO_API_KEY"]
    uid = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common").authenticate(db, username, api_key, {})
    if not uid:
        raise SystemExit("Authentication failed.")
    return db, uid, api_key, xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")


def execute(models, db, uid, api_key, model, method, args, kwargs=None):
    return models.execute_kw(db, uid, api_key, model, method, args, kwargs or {})


def read_csv(path):
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def extract_check_payee(ref):
    match = re.search(r"\bCheck\s+\d+\s+-\s+(.+)$", ref or "", re.I)
    if not match:
        return ""
    payee = match.group(1)
    payee = re.sub(r"\s*\([^)]*\)\s*$", "", payee).strip()
    payee = re.sub(r"\s+Inv(?:oice)?#?.*$", "", payee, flags=re.I).strip()
    return payee


def exact_or_close_partner(models, db, uid, api_key, payee):
    if not payee:
        return "", "", ""
    searches = [payee]
    if " DBA " in payee.upper():
        searches.append(re.split(r"\bDBA\b", payee, flags=re.I)[0].strip())
    for term in searches:
        rows = execute(
            models,
            db,
            uid,
            api_key,
            "res.partner",
            "search_read",
            [[("name", "ilike", term)]],
            {"fields": ["id", "name"], "limit": 5, "order": "name asc"},
        )
        exact = [row for row in rows if row["name"].strip().lower() == term.strip().lower()]
        if len(exact) == 1:
            return str(exact[0]["id"]), exact[0]["name"], "Exact existing partner match."
        if len(rows) == 1:
            return str(rows[0]["id"]), rows[0]["name"], "Single close existing partner match."
        if rows:
            return "", "; ".join(f"{row['id']}:{row['name']}" for row in rows), "Multiple possible existing partners."
    return "", "", "No existing partner found."


def expected_account(ref):
    for pattern, account, reason in SAFE_RECODE_RULES:
        if re.search(pattern, ref or "", re.I):
            return account, reason
    return "", ""


def build_rows(bank_rows, partner_lookup):
    plan = []
    for row in bank_rows:
        risk = row.get("Risk", "")
        if risk == "OK":
            continue
        category = row.get("Category", "")
        ref = row.get("Payment Ref", "")
        current_account = row.get("Counterpart Accounts", "")
        evidence_status = row.get("Shop Boss Evidence Status", "")
        action = "No action"
        target_account = ""
        target_partner_id = ""
        target_partner_name = ""
        confidence = "Low"
        reason = row.get("Finding", "")

        if risk == "High" and "deposit" in category.lower():
            action = "Review merchant settlement clearing"
            confidence = "High"
            reason = "Shop Boss evidence exists, but Odoo matched the settlement directly to AR. Rebuild through payment/batch clearing if this duplicates the registered Shop Boss payment."
        elif risk == "Medium" and category == "Known operating vendor":
            target_account, rule_reason = expected_account(ref)
            if target_account and target_account not in current_account:
                action = "Safe account recode candidate"
                confidence = "High" if "No Shop Boss PO evidence" in evidence_status else "Medium"
                reason = rule_reason
        elif risk == "Medium" and category == "Check":
            payee = extract_check_payee(ref)
            if payee:
                partner_id, partner_name, partner_reason = partner_lookup.get(payee, ("", "", "Not looked up."))
                target_partner_id = partner_id
                target_partner_name = partner_name
                action = "Set bank-line partner candidate" if partner_id else "Review/create partner candidate"
                confidence = "High" if partner_id else "Medium"
                reason = f"Parsed check payee: {payee}. {partner_reason}"
            else:
                action = "Needs check-register detail"
                confidence = "Medium"
                reason = "Bank ref only says Check; no payee is visible."

        if action != "No action":
            plan.append({
                "Action": action,
                "Confidence": confidence,
                "Risk": risk,
                "Category": category,
                "Date": row.get("Date", ""),
                "Amount": row.get("Amount", ""),
                "Payment Ref": ref,
                "Bank Statement Line ID": row.get("Bank Statement Line ID", ""),
                "Counterpart Move Line IDs": row.get("Counterpart Move Line IDs", ""),
                "Current Counterpart Accounts": current_account,
                "Target Account": target_account,
                "Target Partner ID": target_partner_id,
                "Target Partner Name": target_partner_name,
                "Shop Boss Evidence Status": evidence_status,
                "Shop Boss Evidence Doc": row.get("Shop Boss Evidence Doc", ""),
                "Reason": reason,
            })
    return plan


def write_markdown(plan):
    counts = {}
    for row in plan:
        counts[row["Action"]] = counts.get(row["Action"], 0) + 1
    lines = [
        "# Reconciled Bank Matching Repair Plan",
        "",
        "Scope: 2026 reconciled Laurel bank lines already audited against Odoo and Shop Boss.",
        "",
        "## Action Buckets",
        "",
    ]
    for action, count in sorted(counts.items()):
        lines.append(f"- {action}: {count}")
    safe_recodes = [row for row in plan if row["Action"] == "Safe account recode candidate"]
    lines.extend([
        "",
        "## Safe Recode Candidates",
        "",
    ])
    if safe_recodes:
        for row in safe_recodes:
            lines.append(f"- {row['Date']} {row['Amount']} {row['Payment Ref']} : `{row['Current Counterpart Accounts']}` -> `{row['Target Account']}`")
    else:
        lines.append("- None.")
    lines.extend([
        "",
        "## Notes",
        "",
        "- This is a read-only plan. It does not change Odoo.",
        "- Checks with exact partner matches can be cleaned by setting the bank-line partner, but the account coding should still be reviewed against the check register/vendor bill.",
        "- The merchant settlement/AR item should be handled carefully because it may affect invoice payment state.",
    ])
    MD_OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    rows = read_csv(CROSSCHECK)
    check_payees = sorted({extract_check_payee(row.get("Payment Ref", "")) for row in rows if row.get("Category") == "Check"})
    check_payees = [payee for payee in check_payees if payee]
    db, uid, api_key, models = connect()
    partner_lookup = {payee: exact_or_close_partner(models, db, uid, api_key, payee) for payee in check_payees}
    plan = build_rows(rows, partner_lookup)
    fields = [
        "Action",
        "Confidence",
        "Risk",
        "Category",
        "Date",
        "Amount",
        "Payment Ref",
        "Bank Statement Line ID",
        "Counterpart Move Line IDs",
        "Current Counterpart Accounts",
        "Target Account",
        "Target Partner ID",
        "Target Partner Name",
        "Shop Boss Evidence Status",
        "Shop Boss Evidence Doc",
        "Reason",
    ]
    write_csv(OUT, plan, fields)
    write_markdown(plan)
    print(f"Connected uid: {uid}")
    print(f"Repair plan rows: {len(plan)}")
    print(f"Output: {OUT}")
    print(f"Summary: {MD_OUT}")


if __name__ == "__main__":
    main()
