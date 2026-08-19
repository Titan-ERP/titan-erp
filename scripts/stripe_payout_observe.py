from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xmlrpc.client
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from odoo_runtime import OdooClient, OdooConfig
from odoo_runtime.artifacts import ArtifactStore, sha256_file

ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT / "odoo_connection.env"
ARTIFACT_ROOT = ROOT / "odoo_imports" / "accounting" / "stripe_reconciliation_analysis"
COMPANY_NAME = "Southern Equipment Company (Laurel)"
STRIPE_API_BASE = "https://api.stripe.com/v1"
SCHEMA_VERSION = "stripe-payout-observe-v1"
BRIDGE_BANK_LINE_RE = re.compile(r"\bbridge\s+(\d+)\b|\bbatch\s+(\d+)\b", re.IGNORECASE)


class StripeClient:
    def __init__(self, secret_key: str) -> None:
        if not secret_key.startswith(("sk_live_", "sk_test_", "rk_live_", "rk_test_")):
            raise RuntimeError("Stripe secret key does not look like a Stripe API secret key.")
        self.secret_key = secret_key

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{STRIPE_API_BASE}{path}"
        if query:
            url = f"{url}?{query}"
        token = base64.b64encode(f"{self.secret_key}:".encode()).decode("ascii")
        request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read().decode("utf-8"))

    def list_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        output: list[dict[str, Any]] = []
        next_params = dict(params or {})
        next_params.setdefault("limit", 100)
        while True:
            payload = self.get(path, next_params)
            rows = payload.get("data", [])
            output.extend(rows)
            if not payload.get("has_more") or not rows:
                return output
            next_params["starting_after"] = rows[-1]["id"]


def iso_date_from_ts(value: int | None) -> str:
    if not value:
        return ""
    return datetime.fromtimestamp(int(value), UTC).date().isoformat()


def cents_to_dollars(value: float | None) -> float:
    return round(float(value or 0) / 100.0, 2)


def parse_date(value: str) -> date:
    return date.fromisoformat(value)


def stripe_secret_from_aws(secret_id: str, profile: str = "", region: str = "") -> str:
    command = ["aws"]
    if profile:
        command.extend(["--profile", profile])
    if region:
        command.extend(["--region", region])
    command.extend(["secretsmanager", "get-secret-value", "--secret-id", secret_id, "--output", "json"])
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    payload = json.loads(result.stdout)
    secret_string = payload.get("SecretString", "")
    if not secret_string:
        raise RuntimeError(f"AWS secret {secret_id!r} did not contain SecretString.")
    try:
        decoded = json.loads(secret_string)
    except json.JSONDecodeError:
        return secret_string.strip()
    for key in ("STRIPE_SECRET_KEY", "stripe_secret_key", "secret_key", "api_key"):
        if decoded.get(key):
            return str(decoded[key]).strip()
    raise RuntimeError(f"AWS secret {secret_id!r} did not contain a recognized Stripe secret key field.")


def load_stripe_secret(args: argparse.Namespace) -> str:
    if args.aws_secret_id:
        return stripe_secret_from_aws(args.aws_secret_id, args.aws_profile, args.aws_region)
    value = os.environ.get(args.stripe_secret_env, "").strip()
    if value:
        return value
    raise RuntimeError(
        f"Missing Stripe credential. Set {args.stripe_secret_env} or pass --aws-secret-id. "
        "Do not paste the key into chat or commit it to the repo."
    )


def find_company(client: OdooClient) -> int:
    rows = client.execute(
        "res.company",
        "search_read",
        [[("name", "=", COMPANY_NAME)]],
        {"fields": ["id", "name"], "limit": 1},
    )
    if not rows:
        raise RuntimeError(f"Could not find company: {COMPANY_NAME}")
    return int(rows[0]["id"])


def fields_available(client: OdooClient, model: str) -> set[str]:
    try:
        return set(client.fields(model))
    except (RuntimeError, OSError, TimeoutError, xmlrpc.client.Fault, xmlrpc.client.ProtocolError):
        return set()


def search_read_existing(
    client: OdooClient,
    model: str,
    domain: list[Any],
    fields: list[str],
    **kwargs: Any,
) -> list[dict[str, Any]]:
    available = fields_available(client, model)
    if not available:
        return []
    selected = [field for field in fields if field in available]
    if not selected:
        return []
    return client.search_read_all(model, domain, selected, **kwargs)


def fetch_odoo_context(client: OdooClient, company_id: int, start: date, end: date) -> dict[str, Any]:
    terminal_fields = [
        "id",
        "name",
        "company_id",
        "state",
        "payment_intent_id",
        "amount",
        "processing_fee_amount",
        "account_payment_id",
        "invoice_id",
        "fee_invoice_id",
        "partner_id",
        "create_date",
    ]
    terminals = search_read_existing(
        client,
        "southern.stripe.terminal.payment",
        [("company_id", "=", company_id), ("create_date", ">=", start.isoformat()), ("create_date", "<=", (end + timedelta(days=1)).isoformat())],
        terminal_fields,
        order="create_date,id",
    )

    tx_fields = [
        "id",
        "provider_reference",
        "reference",
        "amount",
        "state",
        "payment_id",
        "invoice_ids",
        "company_id",
        "create_date",
    ]
    transactions = search_read_existing(
        client,
        "payment.transaction",
        [("company_id", "=", company_id), ("create_date", ">=", start.isoformat()), ("create_date", "<=", (end + timedelta(days=1)).isoformat())],
        tx_fields,
        order="create_date,id",
    )

    payment_fields = [
        "id",
        "name",
        "ref",
        "amount",
        "state",
        "is_reconciled",
        "partner_id",
        "company_id",
        "date",
        "journal_id",
        "payment_method_line_id",
    ]
    payments = search_read_existing(
        client,
        "account.payment",
        [("company_id", "=", company_id), ("date", ">=", start.isoformat()), ("date", "<=", end.isoformat())],
        payment_fields,
        order="date,id",
    )

    bank_fields = [
        "id",
        "date",
        "name",
        "payment_ref",
        "amount",
        "is_reconciled",
        "journal_id",
        "company_id",
        "internal_index",
        "online_transaction_identifier",
    ]
    bank_lines = search_read_existing(
        client,
        "account.bank.statement.line",
        [("company_id", "=", company_id), ("date", ">=", start.isoformat()), ("date", "<=", (end + timedelta(days=7)).isoformat())],
        bank_fields,
        order="date,id",
    )

    stripe_clearing_moves = search_read_existing(
        client,
        "account.move",
        [
            ("company_id", "=", company_id),
            ("ref", "ilike", "Stripe payout clearing"),
            ("date", ">=", start.isoformat()),
            ("date", "<=", (end + timedelta(days=7)).isoformat()),
        ],
        ["id", "name", "ref", "date", "state"],
        order="date,id",
    )
    stripe_bridge_moves = search_read_existing(
        client,
        "account.move",
        [
            ("company_id", "=", company_id),
            ("ref", "ilike", "Stripe bank batch bridge"),
            ("date", ">=", start.isoformat()),
            ("date", "<=", (end + timedelta(days=7)).isoformat()),
        ],
        ["id", "name", "ref", "date", "state"],
        order="date,id",
    )

    return {
        "terminal_payments": terminals,
        "payment_transactions": transactions,
        "account_payments": payments,
        "bank_lines": bank_lines,
        "stripe_clearing_moves": stripe_clearing_moves,
        "stripe_bridge_moves": stripe_bridge_moves,
    }


def source_id(balance_transaction: dict[str, Any]) -> str:
    source = balance_transaction.get("source")
    if isinstance(source, dict):
        return str(source.get("id") or "")
    return str(source or "")


def payment_intent_id(balance_transaction: dict[str, Any]) -> str:
    source = balance_transaction.get("source")
    if isinstance(source, dict):
        payment_intent = source.get("payment_intent")
        if isinstance(payment_intent, dict):
            return str(payment_intent.get("id") or "")
        if payment_intent:
            return str(payment_intent)
    return ""


def categorize_balance_transactions(rows: list[dict[str, Any]]) -> dict[str, Any]:
    totals = defaultdict(int)
    charges: list[dict[str, Any]] = []
    for row in rows:
        txn_type = str(row.get("type") or "")
        amount = int(row.get("amount") or 0)
        fee = int(row.get("fee") or 0)
        totals["stripe_fees"] += fee
        if txn_type in {"charge", "payment"} and amount > 0:
            totals["gross_charges"] += amount
            charges.append(row)
        elif txn_type in {"refund", "payment_refund"}:
            totals["refunds"] += abs(amount)
        elif txn_type in {"dispute", "dispute_reversal", "chargeback"}:
            totals["disputes"] += abs(amount) if amount < 0 else -amount
        elif txn_type in {"adjustment", "stripe_fee", "payout_failure", "payout_cancel"}:
            totals["adjustments"] += amount
        else:
            totals[f"type:{txn_type or 'unknown'}"] += amount
    expected_net = totals["gross_charges"] - totals["stripe_fees"] - totals["refunds"] - totals["disputes"] + totals["adjustments"]
    return {
        "gross_charges_cents": totals["gross_charges"],
        "stripe_fees_cents": totals["stripe_fees"],
        "refunds_cents": totals["refunds"],
        "disputes_cents": totals["disputes"],
        "adjustments_cents": totals["adjustments"],
        "expected_net_cents": expected_net,
        "charge_count": len(charges),
        "transaction_count": len(rows),
        "other_type_totals": {key: value for key, value in totals.items() if key.startswith("type:")},
    }


def index_odoo(context: dict[str, Any]) -> dict[str, Any]:
    terminal_by_pi = {
        str(row.get("payment_intent_id")): row for row in context["terminal_payments"] if row.get("payment_intent_id")
    }
    transaction_by_ref = {
        str(row.get("provider_reference")): row for row in context["payment_transactions"] if row.get("provider_reference")
    }
    clearing_by_payout = {}
    for row in context.get("stripe_clearing_moves", []):
        ref = str(row.get("ref") or "")
        payout_id = ref.removeprefix("Stripe payout clearing ").strip()
        if payout_id:
            clearing_by_payout[payout_id] = row
    bridges_by_payout: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in context.get("stripe_bridge_moves", []):
        ref = str(row.get("ref") or "")
        payout_id = ref.split()[-1] if ref else ""
        if payout_id:
            bridges_by_payout[payout_id].append(row)
    return {
        "terminal_by_payment_intent": terminal_by_pi,
        "transaction_by_provider_reference": transaction_by_ref,
        "clearing_by_payout": clearing_by_payout,
        "bridges_by_payout": bridges_by_payout,
    }


def name_of(value: Any) -> str:
    if isinstance(value, list) and len(value) >= 2:
        return str(value[1])
    return ""


def csv_int_ids(value: str) -> list[int]:
    output = []
    for item in str(value or "").split(","):
        item = item.strip()
        if item.isdigit():
            output.append(int(item))
    return output


def many2many_set(value: str) -> list[tuple[int, int, list[int]]]:
    return [(6, 0, csv_int_ids(value))]


def bridge_bank_line_ids(bridge_moves: list[dict[str, Any]]) -> list[int]:
    ids: set[int] = set()
    for move in bridge_moves:
        text = f"{move.get('name') or ''} {move.get('ref') or ''}"
        for match in BRIDGE_BANK_LINE_RE.finditer(text):
            for group in match.groups():
                if group and group.isdigit():
                    ids.add(int(group))
    return sorted(ids)


def matching_bank_lines(bank_lines: list[dict[str, Any]], payout: dict[str, Any]) -> list[dict[str, Any]]:
    net = cents_to_dollars(payout.get("amount"))
    arrival = iso_date_from_ts(payout.get("arrival_date"))
    matches = []
    for line in bank_lines:
        if round(float(line.get("amount") or 0), 2) != net:
            continue
        line_date = str(line.get("date") or "")
        if arrival and line_date:
            delta = abs((parse_date(line_date) - parse_date(arrival)).days)
            if delta > 7:
                continue
        text = f"{line.get('name') or ''} {line.get('payment_ref') or ''}".lower()
        score = 2 if "stripe" in text else 1
        if "merchant" in text:
            score += 1
        copy = dict(line)
        copy["match_score"] = score
        matches.append(copy)
    return sorted(matches, key=lambda item: (-int(item["match_score"]), str(item.get("date") or ""), int(item["id"])))


def merchant_bank_lines(bank_lines: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for line in bank_lines:
        amount = round(float(line.get("amount") or 0), 2)
        if amount <= 0:
            continue
        text = f"{line.get('name') or ''} {line.get('payment_ref') or ''}".lower()
        if any(term in text for term in ("merchant", "stripe", "net settle", "card", "deposit")):
            rows.append(line)
    return rows


def batch_bank_candidates(
    payout_rows: list[dict[str, Any]],
    bank_lines: list[dict[str, Any]],
    *,
    window_days: int = 3,
) -> list[dict[str, Any]]:
    candidates = []
    merchant_lines = merchant_bank_lines(bank_lines)
    for bank_line in merchant_lines:
        bank_amount = round(float(bank_line.get("amount") or 0), 2)
        bank_date_raw = str(bank_line.get("date") or "")
        if not bank_date_raw:
            continue
        bank_date = parse_date(bank_date_raw)
        eligible = []
        for payout in payout_rows:
            if payout.get("state") == "matched":
                continue
            arrival_date = str(payout.get("arrival_date") or "")
            if not arrival_date:
                continue
            delta = (bank_date - parse_date(arrival_date)).days
            if 0 <= delta <= window_days:
                eligible.append(payout)
        if len(eligible) < 2:
            continue
        for mask in range(1, 1 << len(eligible)):
            selected = [eligible[index] for index in range(len(eligible)) if mask & (1 << index)]
            if len(selected) < 2:
                continue
            selected_total = round(sum(float(row["stripe_payout_net"]) for row in selected), 2)
            variance = round(bank_amount - selected_total, 2)
            if abs(variance) <= 0.02:
                candidates.append(
                    {
                        "bank_line_id": bank_line["id"],
                        "bank_line_date": bank_date_raw,
                        "bank_line_amount": bank_amount,
                        "bank_line_name": bank_line.get("name") or "",
                        "bank_line_payment_ref": bank_line.get("payment_ref") or "",
                        "stripe_payout_ids": ",".join(row["stripe_payout_id"] for row in selected),
                        "stripe_payout_arrival_dates": ",".join(row["arrival_date"] for row in selected),
                        "stripe_payout_net_total": selected_total,
                        "variance": variance,
                        "payout_count": len(selected),
                        "state": "batch_candidate",
                    }
                )
                break
    return sorted(candidates, key=lambda row: (str(row["bank_line_date"]), int(row["bank_line_id"]), row["payout_count"]))


def component_bank_candidates(
    payout_rows: list[dict[str, Any]],
    bank_lines: list[dict[str, Any]],
    *,
    window_days: int = 4,
) -> list[dict[str, Any]]:
    candidates = []
    for payout in payout_rows:
        if payout.get("state") == "matched":
            continue
        payout_net = round(float(payout["stripe_payout_net"]), 2)
        arrival_date_raw = str(payout.get("arrival_date") or "")
        if not arrival_date_raw:
            continue
        arrival_date = parse_date(arrival_date_raw)
        for bank_line in merchant_bank_lines(bank_lines):
            bank_date_raw = str(bank_line.get("date") or "")
            if not bank_date_raw:
                continue
            delta_days = (parse_date(bank_date_raw) - arrival_date).days
            if not 0 <= delta_days <= window_days:
                continue
            bank_amount = round(float(bank_line.get("amount") or 0), 2)
            if bank_amount < payout_net:
                continue
            candidates.append(
                {
                    "stripe_payout_id": payout["stripe_payout_id"],
                    "arrival_date": arrival_date_raw,
                    "stripe_payout_net": payout_net,
                    "gross_charges": payout["gross_charges"],
                    "stripe_fees": payout["stripe_fees"],
                    "processing_fee_charged": payout["processing_fee_charged"],
                    "processing_fee_margin": payout["processing_fee_margin"],
                    "matched_payment_ids": payout["matched_payment_ids"],
                    "linked_invoice_ids": payout["linked_invoice_ids"],
                    "bank_line_id": bank_line["id"],
                    "bank_line_date": bank_date_raw,
                    "bank_line_amount": bank_amount,
                    "bank_line_residual_after_stripe": round(bank_amount - payout_net, 2),
                    "bank_line_reconciled": bool(bank_line.get("is_reconciled")),
                    "bank_line_partner": name_of(bank_line.get("partner_id")),
                    "bank_line_name": bank_line.get("name") or "",
                    "bank_line_payment_ref": bank_line.get("payment_ref") or "",
                    "bank_line_internal_index": bank_line.get("internal_index") or "",
                    "bank_line_online_transaction_identifier": bank_line.get("online_transaction_identifier") or "",
                    "days_after_arrival": delta_days,
                    "state": "component_candidate",
                }
            )
    return sorted(
        candidates,
        key=lambda row: (
            int(row["days_after_arrival"]),
            bool(row["bank_line_reconciled"]),
            float(row["bank_line_residual_after_stripe"]),
            int(row["bank_line_id"]),
        ),
    )


def build_payout_evidence(
    stripe_client: StripeClient,
    payout: dict[str, Any],
    odoo_context: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    balance_rows = stripe_client.list_all(
        "/balance_transactions",
        {"payout": payout["id"], "expand[]": ["data.source"]},
    )
    totals = categorize_balance_transactions(balance_rows)
    indexes = index_odoo(odoo_context)
    matched_terminal_ids: set[int] = set()
    matched_payment_ids: set[int] = set()
    matched_invoice_ids: set[int] = set()
    unmatched_payment_intents: list[str] = []
    processing_fee_charged_cents = 0

    for row in balance_rows:
        pi_id = payment_intent_id(row)
        if not pi_id:
            continue
        terminal = indexes["terminal_by_payment_intent"].get(pi_id)
        transaction = indexes["transaction_by_provider_reference"].get(pi_id)
        if terminal:
            matched_terminal_ids.add(int(terminal["id"]))
            processing_fee_charged_cents += round(float(terminal.get("processing_fee_amount") or 0.0) * 100)
            account_payment = terminal.get("account_payment_id")
            if isinstance(account_payment, list) and account_payment:
                matched_payment_ids.add(int(account_payment[0]))
            for field in ("invoice_id", "fee_invoice_id"):
                invoice = terminal.get(field)
                if isinstance(invoice, list) and invoice:
                    matched_invoice_ids.add(int(invoice[0]))
        elif transaction:
            payment = transaction.get("payment_id")
            if isinstance(payment, list) and payment:
                matched_payment_ids.add(int(payment[0]))
            invoices = transaction.get("invoice_ids") or []
            if isinstance(invoices, list):
                matched_invoice_ids.update(int(invoice_id) for invoice_id in invoices if isinstance(invoice_id, int))
        else:
            unmatched_payment_intents.append(pi_id)

    bank_matches = matching_bank_lines(odoo_context["bank_lines"], payout)
    clearing_move = indexes["clearing_by_payout"].get(payout["id"])
    bridge_moves = indexes["bridges_by_payout"].get(payout["id"], [])
    bridge_line_ids = bridge_bank_line_ids(bridge_moves)
    matched_bank_line_ids = sorted({int(row["id"]) for row in bank_matches} | set(bridge_line_ids))
    variance = int(payout.get("amount") or 0) - int(totals["expected_net_cents"])
    processing_fee_margin_cents = processing_fee_charged_cents - int(totals["stripe_fees_cents"])
    has_disputes = int(totals["disputes_cents"]) != 0
    has_refunds = int(totals["refunds_cents"]) != 0
    if variance:
        state = "review_required"
        reason = "PAYOUT_VARIANCE"
    elif clearing_move and bridge_moves and all(row.get("state") == "posted" for row in [clearing_move, *bridge_moves]):
        state = "matched"
        reason = "STRIPE_CLEARING_BRIDGED"
    elif clearing_move and not bank_matches:
        state = "in_transit"
        reason = "STRIPE_CLEARING_IN_TRANSIT"
    elif not bank_matches:
        state = "review_required"
        reason = "MISSING_BANK_LINE"
    elif unmatched_payment_intents:
        state = "review_required"
        reason = "UNLINKED_PAYMENT_INTENT"
    elif has_disputes:
        state = "review_required"
        reason = "DISPUTE_OR_CHARGEBACK"
    elif has_refunds:
        state = "review_required"
        reason = "REFUND_PRESENT"
    else:
        state = "candidate"
        reason = "EXACT_PAYOUT_EVIDENCE"

    evidence = {
        "stripe_payout_id": payout["id"],
        "status": payout.get("status", ""),
        "arrival_date": iso_date_from_ts(payout.get("arrival_date")),
        "created_date": iso_date_from_ts(payout.get("created")),
        "currency": payout.get("currency", ""),
        "stripe_payout_net": cents_to_dollars(payout.get("amount")),
        "gross_charges": cents_to_dollars(totals["gross_charges_cents"]),
        "stripe_fees": cents_to_dollars(totals["stripe_fees_cents"]),
        "processing_fee_charged": cents_to_dollars(processing_fee_charged_cents),
        "processing_fee_margin": cents_to_dollars(processing_fee_margin_cents),
        "refunds": cents_to_dollars(totals["refunds_cents"]),
        "disputes": cents_to_dollars(totals["disputes_cents"]),
        "adjustments": cents_to_dollars(totals["adjustments_cents"]),
        "expected_net": cents_to_dollars(totals["expected_net_cents"]),
        "variance": cents_to_dollars(variance),
        "transaction_count": totals["transaction_count"],
        "charge_count": totals["charge_count"],
        "matched_bank_line_ids": ",".join(str(row_id) for row_id in matched_bank_line_ids),
        "matched_bank_line_count": len(matched_bank_line_ids),
        "stripe_clearing_move_ids": ",".join(str(row["id"]) for row in [clearing_move] if row),
        "stripe_bridge_move_ids": ",".join(str(row["id"]) for row in bridge_moves),
        "matched_terminal_payment_ids": ",".join(str(row_id) for row_id in sorted(matched_terminal_ids)),
        "matched_payment_ids": ",".join(str(row_id) for row_id in sorted(matched_payment_ids)),
        "linked_invoice_ids": ",".join(str(row_id) for row_id in sorted(matched_invoice_ids)),
        "unmatched_payment_intents": ",".join(sorted(set(unmatched_payment_intents))),
        "state": state,
        "reason_code": reason,
    }
    return evidence, balance_rows


def write_odoo_payout_evidence(
    client: OdooClient,
    company_id: int,
    rows: list[dict[str, Any]],
    *,
    artifact_uri: str,
    artifact_sha256: str,
) -> int:
    available = fields_available(client, "southern.stripe.payout.evidence")
    if not available:
        raise RuntimeError(
            "Odoo model southern.stripe.payout.evidence is not available. Upgrade "
            "southern_accounting_guardrails before using --write-odoo-evidence."
        )
    company = client.execute("res.company", "read", [[company_id]], {"fields": ["currency_id"]})[0]
    currency_id = company["currency_id"][0] if company.get("currency_id") else False
    written = 0
    for row in rows:
        values = {
            "company_id": company_id,
            "stripe_payout_id": row["stripe_payout_id"],
            "status": row["status"],
            "arrival_date": row["arrival_date"] or False,
            "created_date": row["created_date"] or False,
            "currency_id": currency_id,
            "gross_charges": row["gross_charges"],
            "stripe_fees": row["stripe_fees"],
            "processing_fee_charged": row["processing_fee_charged"],
            "refunds": row["refunds"],
            "disputes": row["disputes"],
            "adjustments": row["adjustments"],
            "expected_net": row["expected_net"],
            "stripe_payout_net": row["stripe_payout_net"],
            "variance": row["variance"],
            "transaction_count": row["transaction_count"],
            "charge_count": row["charge_count"],
            "matched_bank_line_ids": many2many_set(row["matched_bank_line_ids"]),
            "stripe_clearing_move_ids": many2many_set(row["stripe_clearing_move_ids"]),
            "stripe_bridge_move_ids": many2many_set(row["stripe_bridge_move_ids"]),
            "matched_terminal_payment_ids_text": row["matched_terminal_payment_ids"],
            "matched_payment_ids": many2many_set(row["matched_payment_ids"]),
            "linked_invoice_ids": many2many_set(row["linked_invoice_ids"]),
            "unmatched_payment_intents": row["unmatched_payment_intents"],
            "artifact_uri": artifact_uri,
            "artifact_sha256": artifact_sha256,
            "artifact_schema_version": SCHEMA_VERSION,
            "state": row["state"],
            "reason_code": row["reason_code"],
        }
        client.execute("southern.stripe.payout.evidence", "upsert_from_worker", [values])
        written += 1
    return written


def write_summary(path: Path, rows: list[dict[str, Any]], raw_hash: str) -> None:
    candidate_count = sum(1 for row in rows if row["state"] == "candidate")
    matched_count = sum(1 for row in rows if row["state"] == "matched")
    in_transit_count = sum(1 for row in rows if row["state"] == "in_transit")
    review_count = sum(1 for row in rows if row["state"] == "review_required")
    total_net = sum(float(row["stripe_payout_net"]) for row in rows)
    total_fees = sum(float(row["stripe_fees"]) for row in rows)
    lines = [
        "# Stripe Payout Observe Summary",
        "",
        f"- Schema: `{SCHEMA_VERSION}`",
        f"- Generated at UTC: `{datetime.now(UTC).isoformat()}`",
        f"- Payouts reviewed: `{len(rows)}`",
        f"- Candidate exact payout groups: `{candidate_count}`",
        f"- Matched through Stripe Clearing bridge: `{matched_count}`",
        f"- In transit through Stripe Clearing: `{in_transit_count}`",
        f"- Review-required payout groups: `{review_count}`",
        f"- Stripe payout net total: `${total_net:,.2f}`",
        f"- Stripe fees total: `${total_fees:,.2f}`",
        f"- Raw Stripe evidence SHA256: `{raw_hash}`",
        "",
        "## Review Required",
        "",
    ]
    review_rows = [row for row in rows if row["state"] == "review_required"]
    if not review_rows:
        lines.append("None.")
    else:
        for row in review_rows:
            lines.append(
                f"- `{row['stripe_payout_id']}` {row['arrival_date']} "
                f"net `${float(row['stripe_payout_net']):,.2f}`: `{row['reason_code']}` "
                f"variance `${float(row['variance']):,.2f}`, bank lines `{row['matched_bank_line_ids'] or 'none'}`, "
                f"unmatched PIs `{row['unmatched_payment_intents'] or 'none'}`"
            )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def append_batch_summary(path: Path, batch_rows: list[dict[str, Any]]) -> None:
    lines = ["", "## Batch Candidates", ""]
    if not batch_rows:
        lines.append("None.")
    else:
        for row in batch_rows:
            lines.append(
                f"- Bank line `{row['bank_line_id']}` on {row['bank_line_date']} "
                f"`${float(row['bank_line_amount']):,.2f}` matches payouts "
                f"`{row['stripe_payout_ids']}` total `${float(row['stripe_payout_net_total']):,.2f}` "
                f"variance `${float(row['variance']):,.2f}`."
            )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def append_component_summary(path: Path, component_rows: list[dict[str, Any]]) -> None:
    lines = ["", "## Component Candidates", ""]
    if not component_rows:
        lines.append("None.")
    else:
        by_payout: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in component_rows:
            by_payout[row["stripe_payout_id"]].append(row)
        for payout_id, rows in by_payout.items():
            lines.append(f"- `{payout_id}` possible batch components:")
            for row in rows[:5]:
                status = "reconciled" if row["bank_line_reconciled"] else "open"
                lines.append(
                    f"  - Bank line `{row['bank_line_id']}` on {row['bank_line_date']} "
                    f"`${float(row['bank_line_amount']):,.2f}` ({status}), "
                    f"residual after Stripe component `${float(row['bank_line_residual_after_stripe']):,.2f}`."
                )
    with path.open("a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Observe Stripe payout reconciliation evidence against Odoo.")
    parser.add_argument("--env", type=Path, default=ENV_PATH)
    parser.add_argument("--date-from", default=(datetime.now(UTC).date() - timedelta(days=14)).isoformat())
    parser.add_argument("--date-to", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--stripe-secret-env", default="STRIPE_SECRET_KEY")
    parser.add_argument("--aws-secret-id", default="")
    parser.add_argument("--aws-profile", default="")
    parser.add_argument("--aws-region", default="")
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument(
        "--write-odoo-evidence",
        action="store_true",
        help="Create/update southern.stripe.payout.evidence records. Default is read-only.",
    )
    args = parser.parse_args()

    start = parse_date(args.date_from)
    end = parse_date(args.date_to)
    secret = load_stripe_secret(args)
    stripe_client = StripeClient(secret)
    odoo = OdooClient(OdooConfig.from_env(args.env)).connect()
    company_id = find_company(odoo)
    odoo_context = fetch_odoo_context(odoo, company_id, start - timedelta(days=7), end + timedelta(days=7))

    payout_params = {
        "arrival_date[gte]": int(datetime.combine(start, datetime.min.time(), UTC).timestamp()),
        "arrival_date[lte]": int(datetime.combine(end + timedelta(days=1), datetime.min.time(), UTC).timestamp()) - 1,
        "limit": min(max(args.limit, 1), 100),
    }
    payouts = stripe_client.list_all("/payouts", payout_params)
    artifact_dir = ARTIFACT_ROOT / datetime.now(UTC).date().isoformat() / "stripe_api"
    store = ArtifactStore(artifact_dir, schema_version=SCHEMA_VERSION)

    evidence_rows: list[dict[str, Any]] = []
    raw_payloads: list[dict[str, Any]] = []
    for payout in payouts:
        evidence, balance_rows = build_payout_evidence(stripe_client, payout, odoo_context)
        evidence_rows.append(evidence)
        raw_payloads.append({"payout": payout, "balance_transactions": balance_rows})

    raw_manifest = store.write_json("stripe_payout_raw_evidence.json", raw_payloads, record_count=len(raw_payloads))
    fields = [
        "stripe_payout_id",
        "status",
        "arrival_date",
        "created_date",
        "currency",
        "stripe_payout_net",
        "gross_charges",
        "stripe_fees",
        "processing_fee_charged",
        "processing_fee_margin",
        "refunds",
        "disputes",
        "adjustments",
        "expected_net",
        "variance",
        "transaction_count",
        "charge_count",
        "matched_bank_line_ids",
        "matched_bank_line_count",
        "stripe_clearing_move_ids",
        "stripe_bridge_move_ids",
        "matched_terminal_payment_ids",
        "matched_payment_ids",
        "linked_invoice_ids",
        "unmatched_payment_intents",
        "state",
        "reason_code",
    ]
    store.write_csv("stripe_payout_evidence.csv", evidence_rows, fields)
    batch_rows = batch_bank_candidates(evidence_rows, odoo_context["bank_lines"])
    component_rows = component_bank_candidates(evidence_rows, odoo_context["bank_lines"])
    store.write_csv(
        "stripe_payout_batch_candidates.csv",
        batch_rows,
        [
            "bank_line_id",
            "bank_line_date",
            "bank_line_amount",
            "bank_line_name",
            "bank_line_payment_ref",
            "stripe_payout_ids",
            "stripe_payout_arrival_dates",
            "stripe_payout_net_total",
            "variance",
            "payout_count",
            "state",
        ],
    )
    store.write_csv(
        "stripe_payout_component_candidates.csv",
        component_rows,
        [
            "stripe_payout_id",
            "arrival_date",
            "stripe_payout_net",
            "gross_charges",
            "stripe_fees",
            "processing_fee_charged",
            "processing_fee_margin",
            "matched_payment_ids",
            "linked_invoice_ids",
            "bank_line_id",
            "bank_line_date",
            "bank_line_amount",
            "bank_line_residual_after_stripe",
            "bank_line_reconciled",
            "bank_line_partner",
            "bank_line_name",
            "bank_line_payment_ref",
            "bank_line_internal_index",
            "bank_line_online_transaction_identifier",
            "days_after_arrival",
            "state",
        ],
    )
    summary_path = artifact_dir / "stripe_payout_observe_summary.md"
    write_summary(summary_path, evidence_rows, raw_manifest["sha256"])
    append_batch_summary(summary_path, batch_rows)
    append_component_summary(summary_path, component_rows)

    written = 0
    if args.write_odoo_evidence:
        written = write_odoo_payout_evidence(
            odoo,
            company_id,
            evidence_rows,
            artifact_uri=str((artifact_dir / "stripe_payout_raw_evidence.json").resolve()),
            artifact_sha256=raw_manifest["sha256"],
        )

    print(f"payouts_reviewed={len(evidence_rows)}")
    print(f"candidate_exact={sum(1 for row in evidence_rows if row['state'] == 'candidate')}")
    print(f"matched_bridge={sum(1 for row in evidence_rows if row['state'] == 'matched')}")
    print(f"in_transit={sum(1 for row in evidence_rows if row['state'] == 'in_transit')}")
    print(f"review_required={sum(1 for row in evidence_rows if row['state'] == 'review_required')}")
    print(f"batch_candidates={len(batch_rows)}")
    print(f"component_candidates={len(component_rows)}")
    if args.write_odoo_evidence:
        print(f"odoo_evidence_written={written}")
    print(f"summary={summary_path.resolve()}")
    print(f"raw_evidence_sha256={sha256_file(artifact_dir / 'stripe_payout_raw_evidence.json')}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        RuntimeError,
        OSError,
        TimeoutError,
        subprocess.CalledProcessError,
        urllib.error.URLError,
        json.JSONDecodeError,
        xmlrpc.client.Fault,
        xmlrpc.client.ProtocolError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
