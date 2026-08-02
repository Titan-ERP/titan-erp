from pathlib import Path

from scripts.sparex_catalog_agents.agent import deterministic_agent_decision
from scripts.sparex_catalog_agents.orchestrator import AGENT_SEQUENCE, MAX_BATCH, _public_url, run_s3_prefix

ROOT = Path(__file__).resolve().parents[1]


def test_fixed_chain_and_batch_limit():
    assert MAX_BATCH == 5
    assert AGENT_SEQUENCE == (
        "coordinator",
        "sparex_discovery",
        "odoo_match",
        "product_verification",
        "website_release",
    )


def test_public_url_is_scoped_to_odoo_base():
    assert _public_url("https://example.odoo.com/", "/shop/example") == "https://example.odoo.com/shop/example"
    assert _public_url("https://example.odoo.com", "https://public.example/item") == "https://public.example/item"


def test_public_url_percent_encodes_unicode_product_paths():
    assert (
        _public_url("https://example.odoo.com", "/shop/king-pin-–-heavy-duty-42")
        == "https://example.odoo.com/shop/king-pin-%E2%80%93-heavy-duty-42"
    )


def test_rate_limit_fallback_keeps_the_exact_ready_chain_bounded():
    snapshot = {
        "task_id": 10,
        "sku": "S.42",
        "product_id": 42,
        "odoo_match_state": "matched",
        "has_positive_supplier_cost": True,
        "has_positive_sales_price": True,
        "has_exact_sparex_url": True,
        "has_image": True,
        "product_is_hidden": True,
        "ready_to_publish": True,
        "blockers": [],
    }
    assert deterministic_agent_decision("coordinator", snapshot).decision == "continue"
    assert deterministic_agent_decision("sparex_discovery", snapshot).decision == "continue"
    assert deterministic_agent_decision("odoo_match", snapshot).decision == "continue"
    assert deterministic_agent_decision("product_verification", snapshot).decision == "ready_for_release"
    release = deterministic_agent_decision("website_release", snapshot)
    assert release.decision == "ready_for_release"
    assert release.next_agent is None


def test_each_run_gets_an_immutable_s3_prefix():
    assert (
        run_s3_prefix("sparex-product-catalog/catalog-agent-automation/production/", "20260801T043236Z")
        == "sparex-product-catalog/catalog-agent-automation/production/20260801T043236Z"
    )


def test_service_uses_non_overlapping_secure_runtime():
    launcher = (ROOT / "scripts" / "run_catalog_agent_automation.sh").read_text(encoding="utf-8")
    service = (ROOT / "cloud" / "aws" / "titan-catalog-agent.service").read_text(encoding="utf-8")
    timer = (ROOT / "cloud" / "aws" / "titan-catalog-agent.timer").read_text(encoding="utf-8")
    assert "flock -n" in launcher
    assert "aws ssm get-parameter" in launcher
    assert "export ODOO_API_MODE=json2" in launcher
    assert "--limit 5" in launcher
    assert "--publish" in launcher
    assert "OPENAI_API_KEY=" not in service
    assert "Environment=ODOO_COMPANY_ID=1" in service
    assert "OnUnitInactiveSec=2min" in timer
    assert "Persistent=false" in timer
