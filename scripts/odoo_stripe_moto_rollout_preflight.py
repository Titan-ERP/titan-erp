from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

odoo_client_module = importlib.import_module("scripts.odoo_runtime.client")
OdooClient = odoo_client_module.OdooClient
OdooConfig = odoo_client_module.OdooConfig
OdooError = odoo_client_module.OdooError


COMPANY_NAME = "Southern Equipment Company (Laurel)"
MODULE_NAME = "southern_stripe_terminal"
TARGET_VERSION = "19.0.1.6.0"


def _name(record_value: Any) -> str | None:
    if isinstance(record_value, list) and len(record_value) >= 2:
        return str(record_value[1])
    return None


def _id(record_value: Any) -> int | None:
    if isinstance(record_value, list) and record_value:
        return int(record_value[0])
    if isinstance(record_value, int):
        return record_value
    return None


def _search_read_one(client: OdooClient, model: str, domain: list[Any], fields: list[str]) -> dict[str, Any] | None:
    rows = client.execute(model, "search_read", [domain], {"fields": fields, "limit": 1})
    return rows[0] if rows else None


def _search_read_all(client: OdooClient, model: str, domain: list[Any], fields: list[str]) -> list[dict[str, Any]]:
    return client.execute(model, "search_read", [domain], {"fields": fields, "limit": 200})


def _model_exists(client: OdooClient, model: str) -> bool:
    try:
        client.fields(model)
        return True
    except OdooError:
        return False


def _check(condition: bool, failures: list[str], message: str) -> None:
    if not condition:
        failures.append(message)


def run_preflight(
    client: OdooClient,
    *,
    company_name: str = COMPANY_NAME,
    target_version: str = TARGET_VERSION,
    allow_moto_enabled: bool = False,
) -> dict[str, Any]:
    failures: list[str] = []
    warnings: list[str] = []

    company = _search_read_one(client, "res.company", [("name", "=", company_name)], ["id", "name"])
    _check(bool(company), failures, f"Company not found: {company_name}")
    company_id = int(company["id"]) if company else None

    module = _search_read_one(
        client,
        "ir.module.module",
        [("name", "=", MODULE_NAME)],
        ["id", "name", "state", "latest_version", "installed_version"],
    )
    _check(bool(module), failures, f"Module not found: {MODULE_NAME}")
    if module:
        _check(module.get("state") == "installed", failures, f"{MODULE_NAME} is not installed")
        installed_version = module.get("installed_version") or module.get("latest_version")
        _check(
            installed_version == target_version,
            failures,
            f"{MODULE_NAME} is {installed_version}; expected {target_version}",
        )

    provider = None
    terminal_config = None
    invoice_route = None
    stripe_method = None
    accounts: list[dict[str, Any]] = []
    moto_group = None
    moto_users: list[dict[str, Any]] = []

    if company_id:
        provider = _search_read_one(
            client,
            "payment.provider",
            [("code", "=", "stripe"), ("company_id", "=", company_id)],
            ["id", "name", "state", "journal_id", "company_id"],
        )
        _check(bool(provider), failures, "Stripe provider is missing for Southern")
        if provider:
            _check(provider.get("state") == "enabled", failures, "Stripe provider is not enabled")
            _check(_name(provider.get("journal_id")) == "Bank", failures, "Stripe provider journal is not Bank")

        if _model_exists(client, "southern.stripe.terminal.config"):
            config_fields = client.fields("southern.stripe.terminal.config")
            terminal_fields = [
                "id",
                "name",
                "active",
                "is_default",
                "provider_id",
                "journal_id",
                "payment_method_line_id",
                "reader_id",
                "location_id",
                "webhook_ready",
                "company_id",
            ]
            if "moto_enabled" in config_fields:
                terminal_fields.append("moto_enabled")
            else:
                failures.append("Terminal config is missing moto_enabled; PR/module upgrade is not installed")
            terminal_config = _search_read_one(
                client,
                "southern.stripe.terminal.config",
                [("company_id", "=", company_id), ("name", "=", "SEC Laurel S710")],
                terminal_fields,
            )
            _check(bool(terminal_config), failures, "SEC Laurel S710 terminal config is missing")
            if terminal_config:
                _check(bool(terminal_config.get("active")), failures, "SEC Laurel S710 is not active")
                _check(bool(terminal_config.get("is_default")), failures, "SEC Laurel S710 is not the default")
                _check(bool(terminal_config.get("reader_id")), failures, "SEC Laurel S710 reader_id is empty")
                _check(bool(terminal_config.get("webhook_ready")), failures, "Stripe Terminal webhook is not ready")
                _check(_name(terminal_config.get("journal_id")) == "Bank", failures, "Terminal journal is not Bank")
                _check(
                    _name(terminal_config.get("payment_method_line_id")) in {"Stripe", "Stripe (Bank)"},
                    failures,
                    "Terminal incoming payment method is not Stripe",
                )
                if terminal_config.get("moto_enabled") and not allow_moto_enabled:
                    failures.append("moto_enabled is already enabled outside an approved test window")
        else:
            failures.append("southern.stripe.terminal.config model is missing")

        if _model_exists(client, "southern.invoice.payment.route"):
            invoice_route = _search_read_one(
                client,
                "southern.invoice.payment.route",
                [("company_id", "=", company_id)],
                [
                    "id",
                    "company_id",
                    "processing_fee_enabled",
                    "processing_fee_percentage",
                    "processing_fee_fixed",
                    "processing_fee_income_account_id",
                    "processing_fee_tax_ids",
                ],
            )
            _check(bool(invoice_route), failures, "Invoice payment route is missing")
            if invoice_route:
                _check(bool(invoice_route.get("processing_fee_enabled")), failures, "Processing fee is disabled")
                _check(
                    float(invoice_route.get("processing_fee_percentage") or 0) == 3.5,
                    failures,
                    "Processing fee percentage is not 3.5",
                )
                _check(
                    float(invoice_route.get("processing_fee_fixed") or 0) == 0.3,
                    failures,
                    "Processing fee fixed amount is not 0.30",
                )
                _check(
                    _name(invoice_route.get("processing_fee_income_account_id"))
                    == "Transaction Processing Fee Income",
                    failures,
                    "Processing fee income account is not Transaction Processing Fee Income",
                )
                _check(not invoice_route.get("processing_fee_tax_ids"), failures, "Processing fee taxes are configured")
        else:
            failures.append("southern.invoice.payment.route model is missing")

        stripe_method = _search_read_one(
            client,
            "account.payment.method.line",
            [("company_id", "=", company_id), ("name", "ilike", "Stripe")],
            ["id", "name", "journal_id", "payment_account_id", "payment_method_id", "company_id"],
        )
        _check(bool(stripe_method), failures, "Stripe incoming payment method line is missing")
        if stripe_method:
            _check(_name(stripe_method.get("journal_id")) == "Bank", failures, "Stripe method journal is not Bank")
            _check(
                _name(stripe_method.get("payment_account_id")) == "Outstanding Receipts",
                failures,
                "Stripe method does not clear to Outstanding Receipts",
            )

        account_names = [
            "Operating Checking - SEC Laurel",
            "Outstanding Receipts",
            "Transaction Processing Fee Income",
            "Bank Merchant Fees",
        ]
        accounts = _search_read_all(
            client,
            "account.account",
            [("company_ids", "in", [company_id]), ("name", "in", account_names)],
            ["id", "name", "account_type", "company_ids"],
        )
        found_accounts = {account["name"]: account for account in accounts}
        for account_name in account_names:
            _check(account_name in found_accounts, failures, f"Account missing: {account_name}")
        if "Operating Checking - SEC Laurel" in found_accounts:
            _check(
                found_accounts["Operating Checking - SEC Laurel"].get("account_type") == "asset_cash",
                failures,
                "Operating Checking - SEC Laurel is not asset_cash",
            )
        if "Bank Merchant Fees" in found_accounts:
            _check(
                found_accounts["Bank Merchant Fees"].get("account_type") == "expense",
                failures,
                "Bank Merchant Fees is not an expense account",
            )

        group_fields = client.fields("res.groups")
        group_user_field = "users" if "users" in group_fields else "user_ids"
        moto_group = _search_read_one(
            client,
            "res.groups",
            [("name", "=", "Stripe Pay by Phone")],
            ["id", "name", group_user_field],
        )
        if not moto_group:
            failures.append("Stripe Pay by Phone group is missing; PR/module upgrade is not installed")
        elif moto_group.get(group_user_field):
            moto_users = _search_read_all(
                client,
                "res.users",
                [("id", "in", moto_group[group_user_field])],
                ["id", "name", "login", "active"],
            )
            warnings.append(
                f"{len(moto_users)} user(s) currently assigned Stripe Pay by Phone; verify each is approved"
            )

    result = {
        "status": "pass" if not failures else "blocked",
        "company": company,
        "module": module,
        "provider": provider,
        "terminal_config": terminal_config,
        "invoice_payment_route": invoice_route,
        "stripe_payment_method_line": stripe_method,
        "accounts": accounts,
        "moto_group": {
            "id": moto_group.get("id"),
            "name": moto_group.get("name"),
            "user_count": len(moto_group.get("users") or moto_group.get("user_ids") or []),
            "users": moto_users,
        }
        if moto_group
        else None,
        "failures": failures,
        "warnings": warnings,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Stripe MOTO rollout preflight for Southern Odoo.")
    parser.add_argument("--env", default=str(ROOT / "odoo_connection.env"), help="Path to Odoo env file.")
    parser.add_argument("--company-name", default=COMPANY_NAME)
    parser.add_argument("--target-version", default=TARGET_VERSION)
    parser.add_argument(
        "--allow-moto-enabled",
        action="store_true",
        help="Allow moto_enabled=True during an approved supervised production test window.",
    )
    args = parser.parse_args()

    client = OdooClient(OdooConfig.from_env(Path(args.env))).connect()
    result = run_preflight(
        client,
        company_name=args.company_name,
        target_version=args.target_version,
        allow_moto_enabled=args.allow_moto_enabled,
    )
    print(json.dumps(result, indent=2, sort_keys=True, default=str))
    return 0 if result["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
